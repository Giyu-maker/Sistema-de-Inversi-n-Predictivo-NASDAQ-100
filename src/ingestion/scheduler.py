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
- Antes de cada tick corre la **recuperación de huecos** (B2.5): si la
  última vela del bucket se queda a más de un intervalo DENTRO de sesión, se
  pide el tramo faltante (1 crédito, con cuota 8/min y 800/día) y se escribe
  con la guarda insert-if-absent. El primer tick es, por tanto, el arranque.
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
from datetime import date
from typing import TYPE_CHECKING, Any

from src.ai.client import InferenceClient
from src.ai.features import apertura_de_sesion
from src.ai.prediccion import VELAS_A_CONSULTAR, a_candle, predecir_y_persistir
from src.calculator.asymmetric import AsymmetricCalculator
from src.database.predictions import RepositorioPredicciones
from src.ingestion.engine import IngestionEngine, NasdaqCandle, parse_raw_candle
from src.ingestion.huecos import MOTIVOS_BRECHA, CuotaBasica, recuperar_hueco
from src.security.auditoria import IP_LOCAL
from src.integration.notificaciones import AvisadorBrechas, avisador_desde_entorno
from src.security.kill_switch import KillSwitchEngine, motor_desde_entorno
from src.utils.mercado import ahora_ny, feriados_operativos, ventana_de_consulta

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
        cuota: CuotaBasica | None = None,
        feriados: frozenset[date] | None = None,
        kill_switch: KillSwitchEngine | None = None,
        avisador: AvisadorBrechas | None = None,
    ) -> None:
        self.engine = engine
        self.calculator = calculator
        self.interval_s = interval_s
        self.lector = lector
        self.cliente = cliente
        self.predicciones = predicciones
        # Disparo automático del −1,25 % de sesión (RF-202/RF-503). Sin
        # motor no hay disparo, y el ciclo de datos sigue siendo útil: es el
        # mismo criterio de «paso completo o no se configura» del paso de IA.
        self.kill_switch = kill_switch
        #: Aviso de brecha de datos (SS-11): `None` = sin canal configurado.
        #: Como máximo una vez por sesión, dentro del propio avisador.
        self.avisador = avisador
        # Recuperación de huecos (Fase B2.5): corre en el primer tick — es
        # decir, al arrancar — y en cada tick posterior. La cuota se crea aquí
        # para que sus ventanas duren lo que dura el proceso.
        self.cuota = cuota if cuota is not None else CuotaBasica()
        self._feriados = feriados if feriados is not None else feriados_operativos()

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
        """Un ciclo completo: recupera huecos, obtiene la vela, valida, persiste, predice y calcula."""
        await self._recuperar_huecos()
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
        # RF-202/RF-503: el umbral de pérdida de la sesión se evalúa en cada
        # tick, después de persistir la vela, con el MISMO motor que el botón.
        self._evaluar_umbral_de_perdida(latest)
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

    def _evaluar_umbral_de_perdida(self, latest: NasdaqCandle) -> None:
        """Disparo automático al cruzar el −1,25 % de la sesión (RF-202, RF-503).

        Contra el precio de **apertura de la sesión**, con la misma función
        que usa `/api/yield` para mostrarlo (`apertura_de_sesion`): el panel y
        el motor no pueden discrepar sobre qué precio es la referencia.

        * Actor `SISTEMA`, origen `AUTOMATICO`, IP local —es un proceso, no
          una persona—.
        * **Dispara aunque no haya posiciones abiertas**: lo que hace falta
          es bloquear (RF-503); una liquidación sin nada que vender se
          registra igual y prueba que el sistema intentó liquidar.
        * Si es un **gap** (RF-203), el motivo lleva la justificación y el
          `payload` registra la pérdida REAL y su diferencia con el límite.
        * Un fallo aquí no puede tumbar la cadencia: se registra y sigue.
        """
        if self.kill_switch is None or self.lector is None:
            return
        try:
            velas = self.lector.ultimas_velas(VELAS_A_CONSULTAR)
            primera = apertura_de_sesion([a_candle(v) for v in velas])
        except Exception as exc:  # noqa: BLE001 — la cadencia manda
            logger.error("No se pudo evaluar la pérdida de sesión: %s", exc)
            return
        if primera is None or primera.open <= 0:
            return
        if not self.calculator.is_threshold_breached(primera.open, latest.price_close):
            return

        variacion = (latest.price_close - primera.open) / primera.open * 100
        sesion = ahora_ny().date()
        if not self.kill_switch.puede_disparar_automatico(sesion):
            logger.info(
                "Umbral de pérdida alcanzado, sin nuevo disparo: ya está "
                "bloqueado o la sesión ya tuvo un reinicio."
            )
            return

        extra: dict[str, Any] = {
            "variacion_sesion_pct": round(variacion, 4),
            "limite_pct": AsymmetricCalculator.MAX_LOSS_PCT,
            "apertura_sesion_ms": primera.timestamp,
            "precio_apertura": primera.open,
            "precio_ultimo": latest.price_close,
        }
        motivo = (
            f"Pérdida de sesión de {abs(variacion):.2f} % "
            f"(límite {AsymmetricCalculator.MAX_LOSS_PCT:.2f} %)."
        )
        if self.calculator.is_market_gap(variacion):
            # RF-203: manda la pérdida real, y hay que justificar la
            # diferencia. El aviso completo va en el payload; en la columna
            # (VARCHAR(200)) solo cabe la frase corta.
            extra["gap"] = True
            extra["perdida_real_pct"] = round(variacion, 4)
            extra["diferencia_pp"] = round(
                abs(variacion) - AsymmetricCalculator.MAX_LOSS_PCT, 4
            )
            extra["aviso_gap"] = self.calculator.gap_loss_notice(variacion)

        resultado = self.kill_switch.execute_global_liquidation(
            motivo=motivo[:200],
            origen="AUTOMATICO",
            actor="SISTEMA",
            ip_address=IP_LOCAL,
            extra=extra,
        )
        logger.warning("Kill-Switch automático: %s", resultado.detalle)

    async def _recuperar_huecos(self) -> None:
        """Cierra huecos de ingesta antes del tick (B2.5) — ver `src/ingestion/huecos.py`.

        En el primer tick, esto ES el arranque; después, cada tick. Un fallo
        aquí (Influx no responde, proveedor caído, cuota agotada) se registra
        y **no detiene el ciclo normal**: la ingesta de la última vela sigue
        siendo útil aunque la recuperación no lo esté.
        """
        try:
            informe = await recuperar_hueco(
                self.engine, self.cuota, feriados=self._feriados
            )
        except Exception as exc:  # noqa: BLE001 — un hueco no puede tumbar la cadencia
            logger.error("Recuperación de huecos falló: %s", exc)
            self._avisar_brecha("error-recuperacion", str(exc))
            return
        if informe.motivo == "recuperado":
            logger.warning(
                "Hueco recuperado: %s · escritas %d, omitidas %d (1 crédito).",
                informe.detalle, informe.escritas, informe.omitidas,
            )
        elif informe.motivo in MOTIVOS_BRECHA:
            # Hay un hueco real que no se llenó (SS-11): se avisa como mucho
            # una vez por sesión y el ciclo sigue sin esperar el envío.
            logger.warning(
                "Brecha de datos sin recuperar (%s): %s",
                informe.motivo, informe.detalle,
            )
            self._avisar_brecha(informe.motivo, informe.detalle)
        elif informe.motivo not in ("sin-hueco", "fuera-de-sesion"):
            logger.info(
                "Recuperación de huecos sin acción (%s): %s",
                informe.motivo, informe.detalle,
            )

    def _avisar_brecha(self, motivo: str, detalle: str) -> None:
        """Best-effort: el aviso jamás puede frenar el ciclo del scheduler."""
        if self.avisador is None:
            return
        try:
            self.avisador.avisar(
                fecha=ahora_ny().date(), motivo=motivo, detalle=detalle
            )
        except Exception as exc:  # noqa: BLE001 — SS-11: no bloquea nada
            logger.error("No se pudo avisar la brecha de datos: %s", exc)

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
        """Cadencia continua (RF-101) con PUERTA de sesión (revisión P1.9).

        La fuente solo se consulta de lunes a viernes no feriados dentro de
        `ventana_de_consulta` (09:30–16:10 ET: sesión + margen de cierre para
        la última vela): fuera de ahí no se gasta un crédito de la API
        (~288/día antes de esta puerta). La recuperación de huecos corre en
        el arranque, en cada tick de sesión y UNA sola vez tras vencido el
        margen. La fecha y hora de sesión se calculan solo en
        `src/utils/mercado.py` (America/New_York). `max_iterations` existe
        solo para pruebas.
        """
        iterations = 0
        arranque = True
        cierre_recuperado: date | None = None
        while max_iterations is None or iterations < max_iterations:
            try:
                instante = ahora_ny()
                ventana = ventana_de_consulta(instante, self._feriados)
                if ventana == "sesion":
                    await self.run_once()
                elif ventana == "cierre":
                    # Primer tick del margen vencido: cierra los huecos del
                    # día una sola vez y no vuelve a tocar la fuente hoy.
                    if arranque or cierre_recuperado != instante.date():
                        await self._recuperar_huecos()
                        cierre_recuperado = instante.date()
                elif arranque:
                    await self._recuperar_huecos()
                    logger.info(
                        "Fuera de sesión (fin de semana, feriado o antes de "
                        "las 09:30 ET): no se consulta la fuente; los huecos "
                        "quedaron revisados en el arranque."
                    )
                arranque = False
            except Exception as exc:  # un fallo transitorio no debe matar la cadencia
                logger.error("Ciclo falló (se reintenta en %.0f s): %s", self.interval_s, exc)
                arranque = False
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
                "Configuración inicial sembrada: Modo Asesor, perfil Moderado "
                "(umbral 75 %), límite de pérdida 1,25%%."
            )
    except Exception as exc:  # noqa: BLE001 — ver comentario superior
        logger.error("No se pudo sembrar la configuración inicial: %s", exc)

    lector = LectorInflux(influx)
    scheduler = IngestionScheduler(
        IngestionEngine(influx, api_key),
        AsymmetricCalculator,
        lector=lector,
        # Requiere INFERENCE_URL: sin ella el ciclo no estaría cerrado y el
        # error debe salir aquí, no al quinto tick en silencio.
        cliente=InferenceClient(),
        predicciones=PrediccionesPostgres(),
        # Mismo motor que el API (RF-501): siembra, volcado pendiente y el
        # disparo automático del −1,25 % de la sesión en cada tick.
        kill_switch=motor_desde_entorno(lector),
        # SS-11: aviso de brecha de datos, como mucho una vez por sesión.
        avisador=avisador_desde_entorno(),
    )
    asyncio.run(scheduler.run())


if __name__ == "__main__":
    main()
