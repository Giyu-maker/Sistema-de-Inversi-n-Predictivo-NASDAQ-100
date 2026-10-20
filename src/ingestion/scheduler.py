"""Orquestador del walking skeleton — cadencia de 5 minutos (RF-101, Ítem 5).

Ciclo de extremo a extremo del Sprint 3 Fase B:

    Twelve Data → fetch → validate → InfluxDB → features → inferencia (M1)
                → ai_predictions → AsymmetricCalculator

El tramo posterior a InfluxDB (inferencia + persistencia de la predicción) vive
en `src/ai/prediccion.py`; aquí solo se invoca, para que este archivo siga
siendo el reloj y no el negocio.

Decisiones de alcance (trazadas, no olvidos):
- Cadencia con `while True` + `asyncio.sleep`: APScheduler/Celery son
  dependencias que no se justifican para un loop de 5 minutos.
- La variación se calcula entre las dos últimas velas del payload como
  aproximación provisional; la variación de sesión (RF-201) requiere el
  precio de entrada de la posición, que llega con el estado de
  transacciones (Sprint 4).
- El paso de IA es **síncrono dentro del loop async**: `httpx` de
  `InferenceClient` y `psycopg2` no son async, la llamada dura milisegundos y
  no hay otra tarea compitiendo por el hilo. Si alguna vez la hubiera, ese paso
  es el único que habría que mover a `asyncio.to_thread`.
- El paso de IA se configura **completo o no se configura** (lector + cliente +
  repositorio): un scheduler a medio cablear no predeciría en silencio.
  Constructor con 2 argumentos = ciclo del Sprint 2, dicha expresamente en el
  log al arrancar.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from src.ai.client import InferenceClient
from src.ai.prediccion import VELAS_A_CONSULTAR, predecir_y_persistir
from src.calculator.asymmetric import AsymmetricCalculator
from src.database.predictions import RepositorioPredicciones
from src.ingestion.engine import IngestionEngine, NasdaqCandle, parse_raw_candle

if TYPE_CHECKING:  # solo anotaciones: el lector real se construye en main()
    from src.database.influx_lectura import LectorVelas

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
    """Ejecuta el ciclo fetch → validate → write → predict → calculate cada `interval_s`."""

    def __init__(
        self,
        engine: IngestionEngine,
        calculator: type[AsymmetricCalculator],
        interval_s: float = DEFAULT_INTERVAL_S,
        *,
        lector: LectorVelas | None = None,
        cliente: InferenceClient | None = None,
        predicciones: RepositorioPredicciones | None = None,
    ) -> None:
        self.engine = engine
        self.calculator = calculator
        self.interval_s = interval_s
        self.lector = lector
        self.cliente = cliente
        self.predicciones = predicciones

        colaborores: dict[str, object | None] = {
            "lector": lector, "cliente": cliente, "predicciones": predicciones,
        }
        dados = [nombre for nombre, valor in colaborores.items() if valor is not None]
        faltan = [nombre for nombre, valor in colaborores.items() if valor is None]
        if dados and faltan:
            raise ValueError(
                "el paso de predicción se configura completo o no se configura: "
                f"presentes ({', '.join(dados)}), faltan ({', '.join(faltan)}). "
                "Un scheduler a medio cablear no predeciría y nadie lo notaría."
            )
        if not dados:
            logger.info(
                "Scheduler SIN paso de predicción: solo el ciclo de datos del "
                "Sprint 2. Para el ciclo completo del Sprint 3 hay que pasar "
                "lector, cliente e inferencias (ver main())."
            )

    async def run_once(self) -> None:
        """Un ciclo completo: obtiene la vela, valida, persiste, predice y calcula."""
        payload = await self.engine.fetch_nasdaq_quote()
        if not self.engine.validate_payload(payload):
            logger.warning("Payload de la fuente de datos rechazado por validación; no se persiste.")
            return

        candles = _sorted_candles(payload)
        latest = candles[-1]
        await self.engine.write_to_influx(
            latest.timestamp, latest.price_open, latest.price_high,
            latest.price_low, latest.price_close, latest.volume,
        )
        self._predecir()
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

    def _predecir(self) -> None:
        """Paso de IA: ventana de la sesión → M1 → `ai_predictions`.

        Cualquier fallo aquí (Influx no responde, M1 no responde, PostgreSQL
        caído) se registra y **no detiene el resto del ciclo**: la ingesta y el
        cálculo del rendimiento siguen siendo útiles aunque la IA no lo esté,
        y `/api/status` es quien traduce esto a «IA no disponible».
        """
        if self.lector is None or self.cliente is None or self.predicciones is None:
            return
        try:
            velas = self.lector.ultimas_velas(VELAS_A_CONSULTAR)
        except Exception as exc:  # noqa: BLE001 — un fallo de lectura no puede tumbar la cadencia
            logger.error("No se pudieron leer las velas para predecir: %s", exc)
            return
        try:
            predecir_y_persistir(velas, self.cliente, self.predicciones)
        except Exception as exc:  # noqa: BLE001 — idem para PostgreSQL o un error inesperado
            logger.error("El paso de predicción falló: %s", exc)

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

    from src.database.configuraciones import ConfiguracionesPostgres
    from src.database.influx_lectura import LectorInflux
    from src.database.predictions import PrediccionesPostgres
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

    # Siembra idempotente de `system_configurations` (D3): se hace aquí y no en
    # el API porque el API de esta fase es SOLO GET. Un fallo de PostgreSQL al
    # arrancar no debe impedir que la ingesta arranque: el API informará de que
    # no hay configuración y se usarán los valores por defecto.
    configuraciones = ConfiguracionesPostgres()
    try:
        if configuraciones.sembrar_inicial():
            logger.info(
                "Configuración inicial sembrada: Modo Asesor, perfil MODERATE "
                "(umbral 0,75), límite de pérdida 1,25%%."
            )
    except Exception as exc:  # noqa: BLE001 — ver comentario superior
        logger.error("No se pudo sembrar la configuración inicial: %s", exc)

    scheduler = IngestionScheduler(
        IngestionEngine(influx, api_key),
        AsymmetricCalculator,
        lector=LectorInflux(influx),
        # Requiere INFERENCE_URL: sin ella el ciclo no estaría cerrado y el
        # error debe salir aquí, no al quinto tick en silencio.
        cliente=InferenceClient(),
        predicciones=PrediccionesPostgres(),
    )
    asyncio.run(scheduler.run())


if __name__ == "__main__":
    main()
