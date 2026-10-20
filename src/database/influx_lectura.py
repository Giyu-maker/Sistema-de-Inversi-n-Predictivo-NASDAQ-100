"""Lectura de velas desde InfluxDB — las consultas se resuelven en **Flux**.

SDD §6.2: la agregación se hace en base de datos y no en Python, para no
traer a memoria más series de las que la petición pide. Este módulo es la mitad
de lectura que faltaba en el Sprint 2 (el motor solo escribía) y la que
sostienen `/api/candles`, `/api/status`, `/api/yield` y el cálculo del
«resultado real».

Dos formas de consultar:

* **Rango con límite** — para el gráfico y para el historial: `range` + `pivot`
  + `sort`, con `limit` aplicado por Flux.
* **Agregación** — `aggregateWindow` con la función correcta **por columna**
  (apertura: `first`, máximo: `max`, mínimo: `min`, cierre: `last`, volumen:
  `sum`). Se arma como `union` de un filtro por campo antes del `pivot`: un solo
  `aggregateWindow` con una función para todas las columnas daría, por ejemplo,
  la suma de los precios de apertura, que no significa nada.

Los generadores de script son funciones puras de este módulo, sin red: eso
permite probar la consulta que se manda sin necesidad de un InfluxDB.
"""

from __future__ import annotations

import logging
import os
from typing import Protocol

from influxdb_client import InfluxDBClient

from src.ingestion.engine import NasdaqCandle
from src.utils.fechas import fecha_iso_utc

logger = logging.getLogger(__name__)

MEASUREMENT = "NasdaqTick"
CAMPOS: tuple[str, ...] = ("price_open", "price_high", "price_low", "price_close", "volume")

#: Agregaciones disponibles en `/api/candles`. `None` = tal cual se almacenó.
#: La vela de 5 minutos es la unidad de almacenamiento (RF-101), por eso `5m`
#: es el caso "sin agregar".
AGREGACIONES: dict[str, str | None] = {
    "5m": None,
    "15m": "15m",
    "1h": "1h",
    "1d": "1d",
}

#: Función de agregación por columna: una sola `aggregateWindow` con una
#: función para todas las columnas no significa nada en OHLCV.
_FN_POR_CAMPO: dict[str, str] = {
    "price_open": "first",
    "price_high": "max",
    "price_low": "min",
    "price_close": "last",
    "volume": "sum",
}


class LectorVelas(Protocol):
    """Lo que consumen el ciclo de predicción, el API y el benchmark."""

    def ultimas_velas(self, n: int) -> list[NasdaqCandle]: ...

    def velas_rango(
        self, start_ms: int, stop_ms: int, *, agregacion: str = "5m", limit: int | None = None
    ) -> list[NasdaqCandle]: ...

    def ultima_vela(self) -> NasdaqCandle | None: ...


def _bucket_valido(bucket: str) -> str:
    """El bucket viaja dentro del script; sin comillas ni espacios."""
    if not bucket or any(caracter in bucket for caracter in '" \t\n'):
        raise ValueError(f"nombre de bucket inválido: {bucket!r}")
    return bucket


def _filtro(bucket: str, start_ms: int | None, stop_ms: int | None) -> str:
    inicio = "0" if start_ms is None else f'time(v: "{fecha_iso_utc(start_ms)}")'
    parada = "" if stop_ms is None else f', stop: time(v: "{fecha_iso_utc(stop_ms)}")'
    campos = " or ".join(f'r._field == "{campo}"' for campo in CAMPOS)
    return (
        f'from(bucket: "{_bucket_valido(bucket)}") |> range(start: {inicio}{parada}) '
        f'|> filter(fn: (r) => r._measurement == "{MEASUREMENT}" '
        f"and ({campos}))"
    )


def _pivot_y_orden(script: str) -> str:
    return (
        f"{script} "
        '|> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value") '
        '|> sort(columns: ["_time"])'
    )


def flux_ultimas(bucket: str, n: int) -> str:
    """Las últimas `n` velas, ordenadas cronológicamente ascendente."""
    if n < 1:
        raise ValueError("n debe ser al menos 1")
    # Descendente → limit → ascendente (tras el pivot): el `limit` se aplica a
    # la cola de la serie, que es lo que pide «las últimas n».
    return _pivot_y_orden(
        f"{_filtro(bucket, None, None)} "
        f'|> sort(columns: ["_time"], desc: true) |> limit(n: {int(n)})'
    )


def flux_rango(bucket: str, start_ms: int, stop_ms: int, agregacion: str = "5m",
               limit: int | None = None) -> str:
    """Velas de un rango, opcionalmente agregadas y acotadas por `limit`."""
    if agregacion not in AGREGACIONES:
        validas = ", ".join(sorted(AGREGACIONES))
        raise ValueError(f"agregación no soportada: {agregacion!r} (válidas: {validas})")
    if start_ms >= stop_ms:
        raise ValueError("el inicio del rango debe ser anterior al fin")
    base = _filtro(bucket, start_ms, stop_ms)
    intervalo = AGREGACIONES[agregacion]

    if intervalo is None:
        script = _pivot_y_orden(base)
    else:
        por_campo = ",\n  ".join(
            f"{base} |> filter(fn: (r) => r._field == \"{campo}\") "
            f"|> aggregateWindow(every: {intervalo}, fn: {fn}, createEmpty: false)"
            for campo, fn in _FN_POR_CAMPO.items()
        )
        script = _pivot_y_orden(f"union(tables: [\n  {por_campo},\n])")

    if limit is not None:
        if limit < 1:
            raise ValueError("limit debe ser al menos 1")
        script = f"{script} |> limit(n: {int(limit)})"
    return script


def _a_vela(registro) -> NasdaqCandle | None:
    """`FluxRecord` pivotado → `NasdaqCandle`, o None si le falta algún campo.

    Un registro sin los cinco campos no es una vela: meterla en la API con
    ceros fabricaría un gráfico que miente. Se descarta con aviso — es el caso
    de filas escritas antes de CR-001, que solo traían apertura y cierre.
    """
    valores = {campo: registro.values.get(campo) for campo in CAMPOS}
    faltantes = [campo for campo, valor in valores.items() if valor is None]
    if faltantes:
        logger.warning(
            "Vela descartada en la lectura por campos faltantes %s en %s",
            faltantes, registro.get_time(),
        )
        return None
    instante = registro.get_time()
    return NasdaqCandle(
        timestamp=int(instante.timestamp() * 1000),
        price_open=float(valores["price_open"]),
        price_high=float(valores["price_high"]),
        price_low=float(valores["price_low"]),
        price_close=float(valores["price_close"]),
        volume=float(valores["volume"]),
    )


class LectorInflux:
    """Consulta `NasdaqTick` sobre el contenedor local de M2."""

    def __init__(self, cliente: InfluxDBClient, bucket: str | None = None,
                 org: str | None = None) -> None:
        self._cliente = cliente
        self._bucket = bucket or os.environ.get("INFLUXDB_BUCKET", "nasdaq_ticks")
        self._org = org or os.environ.get("INFLUXDB_ORG", "ritech")

    def _consultar(self, script: str) -> list[NasdaqCandle]:
        tablas = self._cliente.query_api().query(script, org=self._org)
        velas: list[NasdaqCandle] = []
        for tabla in tablas:
            for registro in tabla.records:
                vela = _a_vela(registro)
                if vela is not None:
                    velas.append(vela)
        velas.sort(key=lambda c: c.timestamp)
        return velas

    def ultimas_velas(self, n: int) -> list[NasdaqCandle]:
        return self._consultar(flux_ultimas(self._bucket, n))

    def velas_rango(
        self, start_ms: int, stop_ms: int, *, agregacion: str = "5m", limit: int | None = None
    ) -> list[NasdaqCandle]:
        return self._consultar(flux_rango(self._bucket, start_ms, stop_ms, agregacion, limit))

    def ultima_vela(self) -> NasdaqCandle | None:
        velas = self.ultimas_velas(1)
        return velas[-1] if velas else None
