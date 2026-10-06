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
import os
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
from pydantic import BaseModel, ConfigDict, Field

from src.ingestion.providers import MarketDataProvider, TwelveDataProvider

_MARKET_TZ = ZoneInfo("America/New_York")  # los proveedores entregan timestamps en hora de Nueva York
_CANDLE_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

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

    # TODO(RF-102): verificar empíricamente contra la API del proveedor si existe
    # símbolo de índice directo (NDX); QQQ es el proxy estándar del NASDAQ-100.
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
        `TwelveDataProvider` con `prepost=true` para cubrir pre-market y
        after-hours según el escenario de RF-101; la marca de sesión por vela
        no existe en el modelo físico aprobado `NasdaqTick`, solo OHLCV, por
        lo que el antiguo TODO de `extended_hours` queda cerrado aquí).
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

        TODO(SRS-4.2.2): inconsistencia documental pendiente de change request
        formal. El SRS 4.2.2 menciona el máximo y mínimo de la vela, y
        `NasdaqCandle` sí valida `price_high`/`price_low`, pero esta firma
        aprobada del SDD §5.1.2 no los captura. No se resuelve aquí por cuenta
        propia: elevar el cambio sobre SRS/SDD v1.0.
        """
        point = (
            Point("NasdaqTick")
            .time(timestamp, WritePrecision.MS)
            .field("price_open", float(open_p))
            .field("price_close", float(close_p))
            .field("volume", float(vol))
        )
        await asyncio.to_thread(self._write_point, point)

    def _write_point(self, point: Point) -> None:
        """Escritura síncrona de un punto (solo inserción, sin destructuras sobre el histórico)."""
        write_api = self.influx_client.write_api(write_options=SYNCHRONOUS)
        try:
            write_api.write(bucket=self._bucket, org=self._org, record=point)
        finally:
            write_api.close()
