"""Orquestador del walking skeleton — cadencia de 5 minutos (RF-101, Ítem 5).

Ciclo de extremo a extremo del Sprint 2:

    Twelve Data → fetch → validate → InfluxDB → AsymmetricCalculator

Decisiones de alcance (trazadas, no olvidos):
- Cadencia con `while True` + `asyncio.sleep`: APScheduler/Celery son
  dependencias que no se justifican para un loop de 5 minutos.
- La variación se calcula entre las dos últimas velas del payload como
  aproximación provisional; la variación de sesión (RF-201) requiere el
  precio de entrada de la posición, que llega con el estado de
  transacciones (Sprint 4).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from src.calculator.asymmetric import AsymmetricCalculator
from src.ingestion.engine import IngestionEngine, NasdaqCandle, parse_raw_candle

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_S = 300.0  # velas de 5 minutos (RF-101)


def _sorted_candles(payload: dict[str, Any]) -> list[NasdaqCandle]:
    """Normaliza y ordena cronológicamente las velas del payload validado."""
    series = payload.get("candles")
    if not isinstance(series, dict):
        raise ValueError("El payload no contiene 'candles' (formato canónico).")
    candles = [parse_raw_candle(ts, raw) for ts, raw in series.items()]
    return sorted(candles, key=lambda c: c.timestamp)


def _variation_pct(previous_close: float, latest_close: float) -> float:
    """Variación porcentual entre dos cierres consecutivos."""
    if previous_close <= 0:
        raise ValueError("El cierre previo debe ser mayor que 0.")
    return ((latest_close - previous_close) / previous_close) * 100


class IngestionScheduler:
    """Ejecuta el ciclo fetch → validate → write → calculate cada `interval_s`."""

    def __init__(
        self,
        engine: IngestionEngine,
        calculator: type[AsymmetricCalculator],
        interval_s: float = DEFAULT_INTERVAL_S,
    ) -> None:
        self.engine = engine
        self.calculator = calculator
        self.interval_s = interval_s

    async def run_once(self) -> None:
        """Un ciclo completo: obtiene la vela, valida, persiste y calcula el rendimiento."""
        payload = await self.engine.fetch_nasdaq_quote()
        if not self.engine.validate_payload(payload):
            logger.warning("Payload de la fuente de datos rechazado por validación; no se persiste.")
            return

        candles = _sorted_candles(payload)
        latest = candles[-1]
        await self.engine.write_to_influx(
            latest.timestamp, latest.price_open, latest.price_close, latest.volume
        )
        if len(candles) < 2:
            logger.warning("Menos de dos velas en el payload: se persiste la última sin calcular variación.")
            return

        variation = _variation_pct(candles[-2].price_close, latest.price_close)
        session_yield = self.calculator.calculate_yield(variation)
        logger.info(
            "Vela %s persistida · variación %.4f%% · rendimiento RiTech %.4f%%",
            latest.timestamp, variation, session_yield,
        )
        if self.calculator.is_market_gap(variation):
            logger.warning(self.calculator.gap_loss_notice(variation))

    async def run(self, max_iterations: int | None = None) -> None:
        """Cadencia continua (RF-101). `max_iterations` existe solo para pruebas."""
        iterations = 0
        while max_iterations is None or iterations < max_iterations:
            try:
                await self.run_once()
            except Exception as exc:  # un fallo transitorio no debe matar la cadencia
                logger.error("Ciclo falló (se reintenta en %.0f s): %s", self.interval_s, exc)
            iterations += 1
            await asyncio.sleep(self.interval_s)


def main() -> None:
    """Punto de entrada en la Máquina 2: `python -m src.ingestion.scheduler`."""
    import os

    from influxdb_client import InfluxDBClient

    from src.security.crypto import decrypt_secret

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Las URLs de httpx incluyen el parámetro apikey: sus logs en INFO
    # filtrarían la clave en claro a los logs del servicio (RNF-006).
    logging.getLogger("httpx").setLevel(logging.WARNING)
    api_key = decrypt_secret(os.environ["TWELVE_DATA_API_KEY_ENC"])  # requiere CREDENTIALS_MASTER_KEY
    influx = InfluxDBClient(
        url=os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086"),
        token=os.environ["INFLUXDB_ADMIN_TOKEN"],
        org=os.environ.get("INFLUXDB_ORG", "ritech"),
    )
    scheduler = IngestionScheduler(IngestionEngine(influx, api_key), AsymmetricCalculator)
    asyncio.run(scheduler.run())


if __name__ == "__main__":
    main()
