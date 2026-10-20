"""Ciclo de predicción en la Máquina 2 — features → inferencia en M1 → persistencia.

Es el tramo que faltaba para cerrar el ciclo del Sprint 3 y que el backlog dejó
pendiente desde el cierre del Sprint 2:

    fetch → validate → write → **features → InferenceClient.predict → ai_predictions**
           → AsymmetricCalculator

**Este módulo no importa `torch`** (mismo motivo que `src/ai/client.py`): M2
orquesta, no entrena. Todo lo que necesita es el cliente HTTP y las funciones
puras de `features.py`, que ya corren con el grupo base.

Decisiones que quedan escritas aquí
-----------------------------------
* **La ventana sale de la última sesión y debe estar completa.** El modelo se
  entrenó con ventanas de 12 velas **dentro** de una sesión (`dataset.py`
  descarta cualquier muestra que cruce el cierre), así que en vivo se exige lo
  mismo: con menos de 12 velas todavía no hay predicción. Eso **no** es un
  estado degradado: es el sistema esperando datos que aún no existen
  (decisión D6) y por eso se registra en INFO, no como error.
* **Si la inferencia no responde, no se persiste nada.** Una predicción es
  `tendencia + confianza + justificación XAI` (RF-303/RF-304); sin los tres no
  hay fila que valga. El fallo queda en el log y `/api/status` lo refleja como
  «IA no disponible»: el sistema no inventa valores.
* **Fuera de horario no se vuelve a llamar a M1.** Si la última predicción ya
  corresponde al cierre de la ventana que se quiere predecir (misma vela), no
  hay nada nuevo que calcular: con el mercado cerrado la ventana no cambia y
  el ciclo se repetiría inútilmente cada 5 minutos durante 17 horas.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from src.ai.client import ErrorDeInferencia, InferenceClient, Prediccion
from src.ai.features import WINDOW, Candle, extract_window, session_of
from src.database.predictions import (
    PrediccionGuardada,
    PrediccionNueva,
    RepositorioPredicciones,
)
from src.ingestion.engine import NasdaqCandle

logger = logging.getLogger(__name__)

#: Velas a traer de Influx para tener la sesión completa en memoria. Una sesión
#: del plan gratuito son ~78 velas (RF-101); 400 da margen para sesiones largas
#: sin descargar la historia entera de 40.000 velas.
VELAS_A_CONSULTAR = 400


def a_candle(vela: NasdaqCandle) -> Candle:
    """Vela almacenada (SDD) → vela de features. Nombres distintos, mismos datos."""
    return Candle(
        timestamp=vela.timestamp,
        open=vela.price_open,
        high=vela.price_high,
        low=vela.price_low,
        close=vela.price_close,
        volume=vela.volume,
    )


def a_features_candle(velas: list[NasdaqCandle]) -> list[Candle]:
    return [a_candle(v) for v in velas]


@dataclass(frozen=True)
class VentanaPreparada:
    """Las últimas `WINDOW` velas de la sesión en curso, listas para decidir."""

    ventana: list[Candle]
    velas_en_sesion: int

    @property
    def cierre(self) -> int:
        """epoch ms de la ÚLTIMA vela: es el `prediction_time` (decisión D2)."""
        return self.ventana[-1].timestamp


def ultima_ventana(velas: list[Candle]) -> VentanaPreparada | None:
    """Última ventana completa de la última sesión, o None si aún no está completa.

    `session_of` parte por hueco temporal (>1 h), así que una sesión a media
    jornada o un día sin datos no confunde al agrupador: la última sesión que
    aparece es la que manda.
    """
    if not velas:
        logger.info("Sin velas en la serie: aún no hay ventana para predecir.")
        return None
    sesion = session_of(velas)[-1]
    if len(sesion) < WINDOW:
        logger.info(
            "Ventana incompleta: la sesión actual tiene %d de %d velas; se "
            "espera al siguiente tick (no altera el estado del sistema).",
            len(sesion), WINDOW,
        )
        return None
    return VentanaPreparada(ventana=sesion[-WINDOW:], velas_en_sesion=len(sesion))


def predecir(ventana: list[Candle], cliente: InferenceClient) -> Prediccion:
    """Features sin normalizar → predicción de M1.

    La normalización la hace el modelo con los estadísticos del artefacto;
    normalizar aquí sería normalizar dos veces.
    """
    matriz = extract_window(ventana)
    return cliente.predecir(np.asarray(matriz, dtype=np.float64))


@dataclass(frozen=True)
class ResultadoCiclo:
    """Qué pasó en el paso de predicción de un tick."""

    prediccion: PrediccionGuardada
    #: False cuando la predicción de esa vela ya estaba registrada.
    nueva: bool


def predecir_y_persistir(
    velas: list[NasdaqCandle],
    cliente: InferenceClient,
    predicciones: RepositorioPredicciones,
) -> ResultadoCiclo | None:
    """Ciclo completo del paso de IA. **None** = no se generó predicción.

    Los tres motivos de `None` están contemplados y ninguno es un error del
    sistema: ventana incompleta, inferencia no disponible o features inválidas.
    Cada uno deja su rastro en el log con su nivel correspondiente.
    """
    preparada = ultima_ventana(a_features_candle(velas))
    if preparada is None:
        return None

    # Sin esto, con el mercado cerrado se llamaría a M1 cada 5 minutos para
    # recalcular la misma ventana: mismo cierre, misma predicción, más latencia.
    ultima = predicciones.ultima()
    if ultima is not None and ultima.prediction_time == preparada.cierre:
        logger.debug("Predicción ya registrada para la vela %s; se omite el ciclo de IA.",
                     preparada.cierre)
        return ResultadoCiclo(prediccion=ultima, nueva=False)

    try:
        matriz = extract_window(preparada.ventana)
    except ValueError as exc:
        logger.error("Ventana rechazada por validar features: %s", exc)
        return None

    try:
        prediccion = cliente.predecir(np.asarray(matriz, dtype=np.float64))
    except ErrorDeInferencia as exc:
        # RF-303/RF-304: sin predicción no hay fila. El estado del sistema lo
        # dirá («IA no disponible»); aquí no se fabrica ningún valor.
        logger.error("IA no disponible, no se persiste predicción: %s", exc)
        return None

    guardada = predicciones.guardar(
        PrediccionNueva(
            prediction_time=preparada.cierre,
            trend_prediction=prediccion.trend_prediction,
            confidence_score=prediccion.confidence_score,
            technical_rationale=prediccion.technical_rationale,
        )
    )
    if guardada is None:
        # El índice único descartó la fila: alguien escribió la predicción de
        # ESTA misma vela (otro proceso o un tick previo). Se recupera la
        # existente para no perder el resultado del ciclo.
        existente = predicciones.ultima()
        if existente is not None:
            logger.info("La predicción de la vela %s ya existía (conflicto resuelto).",
                        preparada.cierre)
            return ResultadoCiclo(prediccion=existente, nueva=False)
        logger.error("La inserción no confirmó ni devolvió la fila existente.")
        return None

    logger.info(
        "Predicción %s (confianza %.4f) persistida para la vela %s.",
        prediccion.trend_prediction, prediccion.confidence_score, preparada.cierre,
    )
    return ResultadoCiclo(prediccion=guardada, nueva=True)
