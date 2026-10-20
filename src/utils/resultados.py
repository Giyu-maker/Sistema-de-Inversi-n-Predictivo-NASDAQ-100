"""Resultado real de una predicción — con qué se compara después lo predicho.

Cada fila de `ai_predictions` se contrasta con lo que efectivamente hizo el
índice en los 15 minutos siguientes (`HORIZON = 3` velas). Ese contraste puede
dar tres resultados (decisión D6), y la distinción importa porque mezclarlos
sería mentir con una columna:

* **`PENDIENTE`** — la predicción está entre las 3 últimas velas de la serie:
  el horizonte todavía no se completó. Se resuelve solo con datos nuevos.
* **`NO_APLICA`** — el horizonte se sale de la sesión (un cierre de mercado a
  mitad de los 15 minutos): no hay precio con qué comparar, igual que en el
  dataset de entrenamiento, donde ninguna muestra cruza el cierre.
* **`DISPONIBLE`** — hay retorno y etiqueta (`UP`/`DOWN`/`STABLE`) calculados
  **con la misma banda con la que se entrenó** el modelo: `label_for()` y
  `forward_return_pct()` de `src/ai/features.py`, no una reimplementación.

La banda (`stable_band_pct`, 0,1 % por defecto) vive en el artefacto que sirve
M1 y se expone en `GET /modelo`; aquí llega como `banda_pct` para que la función
sea pura y testeable.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.ai.features import (
    HORIZON,
    WINDOW,
    LABEL_NAMES,
    Candle,
    forward_return_pct,
    label_for,
    session_of,
)

#: Vela de 5 minutos (RF-101) en epoch ms.
INTERVALO_VELA_MS = 5 * 60 * 1000
#: El horizonte son `HORIZON` velas después del cierre de la ventana.
HORIZON_MS = HORIZON * INTERVALO_VELA_MS

ESTADO_DISPONIBLE = "DISPONIBLE"
ESTADO_PENDIENTE = "PENDIENTE"
ESTADO_NO_APLICA = "NO_APLICA"


@dataclass(frozen=True)
class ResultadoReal:
    """Contraste entre la predicción y lo ocurrido, listo para la API."""

    estado: str
    motivo: str
    retorno_pct: float | None = None
    etiqueta: str | None = None
    banda_pct: float | None = None


def resultado_real(
    prediction_time: int,
    velas: list[Candle],
    *,
    banda_pct: float,
    ultima_vela_ms: int | None,
) -> ResultadoReal:
    """Evalúa una predicción contra la serie.

    :param velas: velas **alrededor** de la predicción (deben alcanzar para
        reconstruir su sesión: al menos `WINDOW` antes y `HORIZON` después).
    :param ultima_vela_ms: cierre de la vela más reciente de toda la serie, no
        solo del rango consultado.
    """
    # 1) ¿el horizonte existe todavía?
    if ultima_vela_ms is None or ultima_vela_ms < prediction_time + HORIZON_MS:
        return ResultadoReal(
            estado=ESTADO_PENDIENTE,
            motivo=(
                "Horizonte de 15 min aún no completo: la predicción está entre "
                "las 3 últimas velas de la serie."
            ),
        )

    # 2) ¿sigue dentro de su sesión?
    sesion, indice = _sesion_que_contiene(velas, prediction_time)
    if sesion is None:
        return ResultadoReal(
            estado=ESTADO_NO_APLICA,
            motivo="No hay velas de la sesión de la predicción en el rango consultado.",
        )
    if indice < 0:
        return ResultadoReal(
            estado=ESTADO_NO_APLICA,
            motivo="La vela de cierre de la predicción no aparece en el rango consultado.",
        )
    fin_horizonte = indice + HORIZON
    if fin_horizonte >= len(sesion) or sesion[fin_horizonte].timestamp != prediction_time + HORIZON_MS:
        return ResultadoReal(
            estado=ESTADO_NO_APLICA,
            motivo="El horizonte de 15 min cae fuera de la sesión (cierre de mercado).",
        )

    # 3) ¿hay ventana completa antes de la predicción para medir el retorno?
    inicio = indice - WINDOW + 1
    if inicio < 0:
        return ResultadoReal(
            estado=ESTADO_NO_APLICA,
            motivo="Faltan velas anteriores a la predicción en el rango consultado.",
        )

    retorno = forward_return_pct(sesion, inicio)
    etiqueta = LABEL_NAMES[label_for(retorno, banda_pct)]
    return ResultadoReal(
        estado=ESTADO_DISPONIBLE,
        motivo="Horizonte de 15 min completo dentro de la sesión.",
        retorno_pct=retorno,
        etiqueta=etiqueta,
        banda_pct=banda_pct,
    )


def _sesion_que_contiene(velas: list[Candle], timestamp: int) -> tuple[list[Candle] | None, int]:
    """(sesión que contiene el timestamp, su índice dentro de esa sesión)."""
    ordenadas = sorted(velas, key=lambda c: c.timestamp)
    for sesion in session_of(ordenadas):
        primero, ultimo = sesion[0].timestamp, sesion[-1].timestamp
        if primero <= timestamp <= ultimo:
            for i, vela in enumerate(sesion):
                if vela.timestamp == timestamp:
                    return sesion, i
            return sesion, -1  # dentro del rango de la sesión pero sin esa vela
    return None, -1
