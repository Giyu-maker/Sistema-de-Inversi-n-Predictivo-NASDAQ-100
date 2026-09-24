"""IngestionEngine — velas de 5M del NASDAQ-100 (Sprint 2, Ítem 5).

Responsabilidades (docs/sdd.md §5.1.1):
- Interactuar con la API externa de Alpha Vantage de forma robusta (RF-101).
- Normalizar los datos obtenidos en velas de 5 minutos.
- Escribir las series temporales en InfluxDB local con índice epoch ms (RF-103).

Tipado (justificación RNF-011): los payloads crudos de Alpha Vantage son JSON
arbitrario recibido por red y se manejan como `dict[str, Any]`. Ese único `Any`
se acota de inmediato al validarlo con el modelo estricto Pydantic
`NasdaqCandle`, que rechaza nulos, negativos y campos extra.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS
from pydantic import BaseModel, ConfigDict, Field

_MARKET_TZ = ZoneInfo("America/New_York")  # Alpha Vantage entrega timestamps en hora de Nueva York
_CANDLE_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# Claves crudas de Alpha Vantage → campos del modelo normalizado.
_RAW_CANDLE_FIELDS: dict[str, str] = {
    "1. open": "price_open",
    "2. high": "price_high",
    "3. low": "price_low",
    "4. close": "price_close",
    "5. volume": "volume",
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
    """Convierte una vela cruda de Alpha Vantage en `NasdaqCandle` con timestamp epoch ms.

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
    """Consulta Alpha Vantage y persiste velas de 5M en InfluxDB local."""

    # TODO(RF-102): confirmar con RiTech SAS el símbolo exacto con el que Alpha Vantage
    # representa el NASDAQ-100 en su catálogo (QQQ es el proxy estándar del índice).
    SYMBOL = "QQQ"
    _REQUEST_TIMEOUT_S = 10.0

    def __init__(self, influx_client: InfluxDBClient, api_key: str) -> None:
        self.influx_client = influx_client
        self.api_key = api_key
        self.base_url = "https://www.alphavantage.co/query"
        self._bucket = os.environ.get("INFLUXDB_BUCKET", "nasdaq_ticks")
        self._org = os.environ.get("INFLUXDB_ORG", "ritech")

    async def fetch_nasdaq_quote(self) -> dict[str, Any]:
        """Realiza la petición HTTP asíncrona para obtener la última vela de 5M."""
        params: dict[str, str] = {
            "function": "TIME_SERIES_INTRADAY",
            "symbol": self.SYMBOL,
            "interval": "5min",
            "outputsize": "compact",
            "apikey": self.api_key,
            # TODO(RF-102): decidir `extended_hours` según la ventana horaria
            # (pre-market / horario regular / after-hours) y almacenar la marca de
            # sesión con la vela. No se implementa a ciegas: queda pendiente de
            # confirmar cómo responde la API en cada ventana.
        }
        async with httpx.AsyncClient(timeout=self._REQUEST_TIMEOUT_S) as client:
            response = await client.get(self.base_url, params=params)
            response.raise_for_status()
            payload: Any = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Respuesta inesperada de Alpha Vantage: se esperaba un objeto JSON.")
        return payload

    def validate_payload(self, data: dict[str, Any]) -> bool:
        """Verifica que los datos no contengan valores nulos o ilógicos (RF-101)."""
        if not data:
            return False
        # Los errores de Alpha Vantage (cuota agotada, clave inválida) llegan como claves de error.
        if "Error Message" in data or "Note" in data or "Information" in data:
            return False
        series = data.get("Time Series (5min)")
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
