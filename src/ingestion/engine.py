"""IngestionEngine — velas de 5M del NASDAQ-100 (Sprint 2, Ítem 5).

Responsabilidades (docs/sdd.md §5.1.1):
- Interactuar con la fuente externa de datos de forma robusta (RF-101),
  delegando la petición HTTP en el `MarketDataProvider` inyectado.
- Normalizar los datos obtenidos en velas de 5 minutos (formato canónico
  definido en `src/ingestion/providers/base.py`).
- Escribir las series temporales en InfluxDB local con índice epoch ms (RF-103).

Cambio de fuente (CR pendiente): la v1.0 del SDD §5.1 fija Alpha Vantage
dentro de esta clase; la verificación empírica del 06/10/2026 demostró que su
capa gratuita ya no cubre velas de 5M (intraday e índices son premium, cuota
25/día < ~192 necesarias por RF-101), así que el HTTP se movió a
`TwelveDataProvider` tras el acuerdo del equipo de usar el plan gratuito
*Basic* con fines académicos. Las firmas aprobadas de §5.1.2 no cambian.

Tipado (justificación RNF-011): los payloads crudos son JSON arbitrario
recibido por red y se manejan como `dict[str, Any]`. Ese único `Any` se acota
de inmediato al validarlo con el modelo estricto Pydantic `NasdaqCandle`,
que rechaza nulos, negativos y campos extra.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
from pydantic import BaseModel, ConfigDict, Field

from src.ingestion.providers import MarketDataProvider, TwelveDataProvider

logger = logging.getLogger(__name__)

_MARKET_TZ = ZoneInfo("America/New_York")  # los proveedores entregan timestamps en hora de Nueva York
_CANDLE_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# SRS §4.4 / RF-103: "no se permiten escrituras destructivas o modificaciones de
# registros de precios históricos". Verificación empírica del 06/10/2026 (CR-003):
# InfluxDB NO lo garantiza por sí mismo — reescribir un timestamp ya persistido
# sobrescribe el punto en silencio (last write wins). La garantía, por tanto, la
# pone la capa de aplicación: antes de escribir se consulta si el timestamp ya
# existe y, en tal caso, se omite la escritura y se avisa con WARNING.
_EXISTENCE_QUERY_TEMPLATE = """from(bucket: "{bucket}")
  |> range(start: time(v: "{start}"), stop: time(v: "{stop}"))
  |> filter(fn: (r) => r["_measurement"] == "NasdaqTick")
  |> filter(fn: (r) => r["_field"] == "price_close")
"""


def _epoch_ms_to_rfc3339(epoch_ms: int) -> str:
    """Convierte epoch ms a RFC3339 con precisión de milisegundos (UTC).

    Se descompone en segundos + microssegundos para no pasar por float y perder
    precisión con magnitudes epoch grandes.
    """
    instant = datetime.fromtimestamp(epoch_ms // 1000, tz=timezone.utc)
    return instant.strftime("%Y-%m-%dT%H:%M:%S") + f".{(epoch_ms % 1000) * 1000:06d}Z"

# Claves de la vela canónica → campos del modelo normalizado.
_RAW_CANDLE_FIELDS: dict[str, str] = {
    "open": "price_open",
    "high": "price_high",
    "low": "price_low",
    "close": "price_close",
    "volume": "volume",
}


class NasdaqCandle(BaseModel):
    """Vela de 5 minutos normalizada (modelo físico `NasdaqTick`, AGENTS.md §11).

    `strict=True`: sin coerción de tipos; `ge=0`: rechaza precios/volumen
    negativos; los campos son obligatorios: rechaza nulos (RF-101/RF-103).
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    timestamp: int  # epoch ms (RF-103)
    price_open: float = Field(ge=0)
    price_high: float = Field(ge=0)  # validado aquí; ver TODO(SRS-4.2.2) en write_to_influx
    price_low: float = Field(ge=0)  # idem
    price_close: float = Field(ge=0)
    volume: float = Field(ge=0)


def parse_raw_candle(timestamp_str: str, raw: dict[str, Any]) -> NasdaqCandle:
    """Convierte una vela cruda canónica en `NasdaqCandle` con timestamp epoch ms.

    Lanza `ValueError` si la vela cruda es incompleta o inválida.
    """
    try:
        candle_naive = datetime.strptime(timestamp_str, _CANDLE_TIME_FORMAT)
        values = {field: float(raw[key]) for key, field in _RAW_CANDLE_FIELDS.items()}
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Vela cruda inválida para {timestamp_str!r}: {exc}") from exc
    candle_aware = candle_naive.replace(tzinfo=_MARKET_TZ)
    return NasdaqCandle(timestamp=int(candle_aware.timestamp() * 1000), **values)


class IngestionEngine:
    """Consulta la fuente de datos y persiste velas de 5M en InfluxDB local."""

    # RF-102 verificado empíricamente el 06/10/2026 contra la API de Twelve Data:
    # el índice directo NDX está reservado al plan Grow o superior, así que se
    # usa QQQ — el ETF proxy estándar del NASDAQ-100, disponible en el plan
    # gratuito Basic. timestamps de la API verificados como hora de Nueva York.
    SYMBOL = "QQQ"

    def __init__(
        self,
        influx_client: InfluxDBClient,
        api_key: str,
        provider: MarketDataProvider | None = None,
    ) -> None:
        self.influx_client = influx_client
        self.api_key = api_key
        # SDD §5.1.2 declara `base_url` como atributo de esta clase; con la
        # abstracción de proveedor la URL vive en TwelveDataProvider
        # (desviación a reflejar en la CR de cambio de fuente, ver docstring).
        self.provider: MarketDataProvider = (
            provider or TwelveDataProvider(api_key=api_key, symbol=self.SYMBOL)
        )
        self._bucket = os.environ.get("INFLUXDB_BUCKET", "nasdaq_ticks")
        self._org = os.environ.get("INFLUXDB_ORG", "ritech")

    async def fetch_nasdaq_quote(self) -> dict[str, Any]:
        """Realiza la petición HTTP asíncrona para obtener la última vela de 5M.

        Firma fijada por SDD §5.1.2 — se conserva; la petición concreta la
        ejecuta el `MarketDataProvider` inyectado (en configuración real,
        `TwelveDataProvider`). El antiguo TODO de `extended_hours` queda
        cerrado: el modelo físico aprobado `NasdaqTick` solo almacena OHLCV
        (sin marca de sesión) y la cobertura de ventanas extendidas depende
        del plan contratado — ver límites documentados en el proveedor.
        """
        return await self.provider.fetch_candles()

    def validate_payload(self, data: dict[str, Any]) -> bool:
        """Verifica que los datos no contengan valores nulos o ilógicos (RF-101)."""
        if not data:
            return False
        # Los errores de proveedor (cuota agotada, clave inválida) llegan como clave canónica.
        if "error" in data:
            return False
        series = data.get("candles")
        if not isinstance(series, dict) or not series:
            return False
        for timestamp_str, raw in series.items():
            if not isinstance(raw, dict):
                return False
            try:
                parse_raw_candle(timestamp_str, raw)
            except ValueError:  # incluye pydantic.ValidationError (subclase de ValueError)
                return False
        return True

    async def write_to_influx(self, timestamp: int, open_p: float, close_p: float, vol: float) -> None:
        """Persiste la vela en el bucket de InfluxDB local de forma transaccional.

        La escritura síncrona del cliente corre en un hilo aparte con
        `asyncio.to_thread` para no bloquear el event loop (una vela cada 5 min:
        no hay presión de concurrencia que justifique más).

        RF-103 / CR-003 — guarda de inmutabilidad: si el timestamp ya existe en
        el bucket **no se escribe** y se emite un WARNING. InfluxDB sobrescribe
        el punto en silencio (verificado 06/10/2026), así que la no-modificación
        del histórico la garantiza esta capa, no el motor.

        TODO(SRS-4.2.2): inconsistencia documental pendiente de change request
        formal. El SRS 4.2.2 menciona el máximo y mínimo de la vela, y
        `NasdaqCandle` sí valida `price_high`/`price_low`, pero esta firma
        aprobada del SDD §5.1.2 no los captura. No se resuelve aquí por cuenta
        propia: elevar el cambio sobre SRS/SDD v1.0.
        """
        point = self._point(timestamp, open_p, close_p, vol)
        await asyncio.to_thread(self._check_and_write_point, timestamp, point)

    def _point(self, timestamp: int, open_p: float, close_p: float, vol: float) -> Point:
        """Construye el punto `NasdaqTick` con los tres campos del SDD §5.1.2."""
        return (
            Point("NasdaqTick")
            .time(timestamp, WritePrecision.MS)
            .field("price_open", float(open_p))
            .field("price_close", float(close_p))
            .field("volume", float(vol))
        )

    def _check_and_write_point(self, timestamp: int, point: Point) -> None:
        """Escritura unitaria con guarda insert-if-absent (una consulta por vela)."""
        if self._existing_timestamps({timestamp}):
            logger.warning(
                "Escritura omitida: la vela con timestamp %s ya existe en el bucket "
                "'%s' (RF-103/CR-003, inmutabilidad del histórico).",
                timestamp, self._bucket,
            )
            return
        self._write_point(point)

    async def existing_timestamps(self, timestamps: Sequence[int]) -> set[int]:
        """Devuelve el subconjunto de timestamps ya presentes en el bucket.

        Una única consulta de rango para toda la secuencia (no una por vela),
        para que el backfill no multiplique peticiones contra InfluxDB.
        """
        return await asyncio.to_thread(self._existing_timestamps, set(timestamps))

    def _existing_timestamps(self, timestamps: set[int]) -> set[int]:
        if not timestamps:
            return set()
        # Una ventana de rango con holgura de ±1 ms absorbe cualquier diferencia
        # de redondeo; la intersección final devuelve solo lo pedido.
        query = _EXISTENCE_QUERY_TEMPLATE.format(
            bucket=self._bucket,
            start=_epoch_ms_to_rfc3339(min(timestamps) - 1),
            stop=_epoch_ms_to_rfc3339(max(timestamps) + 2),
        )
        tables = self.influx_client.query_api().query(query, org=self._org)
        found: set[int] = set()
        for table in tables:
            for record in table.records:
                found.add(int(round(record.get_time().timestamp() * 1000)))
        return found & timestamps

    async def write_candles_batch(self, candles: Sequence[NasdaqCandle]) -> tuple[int, int]:
        """Escribe un lote de velas omitiendo las que ya existen (backfill).

        Devuelve `(escritas, omitidas)`. Una sola consulta de rango previa y una
        sola escritura por lote, en lugar de una operación por vela.
        """
        return await asyncio.to_thread(self._write_candles_batch, candles)

    def _write_candles_batch(self, candles: Sequence[NasdaqCandle]) -> tuple[int, int]:
        ordered = sorted(candles, key=lambda c: c.timestamp)
        if not ordered:
            return 0, 0
        existing = self._existing_timestamps({c.timestamp for c in ordered})
        missing = [c for c in ordered if c.timestamp not in existing]
        if missing:
            points = [
                self._point(c.timestamp, c.price_open, c.price_close, c.volume)
                for c in missing
            ]
            write_api = self.influx_client.write_api(write_options=SYNCHRONOUS)
            try:
                write_api.write(bucket=self._bucket, org=self._org, record=points)
            finally:
                write_api.close()
        skipped = len(ordered) - len(missing)
        if skipped:
            logger.warning(
                "Lote: %d de %d velas ya existentes, escritura omitida (RF-103/CR-003).",
                skipped, len(ordered),
            )
        return len(missing), skipped

    def _write_point(self, point: Point) -> None:
        """Escritura síncrona de un punto (solo inserción, sin destructuras sobre el histórico)."""
        write_api = self.influx_client.write_api(write_options=SYNCHRONOUS)
        try:
            write_api.write(bucket=self._bucket, org=self._org, record=point)
        finally:
            write_api.close()
