"""Persistencia de `ai_predictions` — insert-if-absent por `prediction_time`.

**Por qué el timestamp es el de la última vela y no el reloj (decisión D2):**
el identificador de una predicción es la ventana sobre la que se decidió, no el
instante en que se calculó. Con eso:

1. el `ON CONFLICT (prediction_time) DO NOTHING` vuelve el ciclo idempotente:
   un reinicio del scheduler, un reintento o una velada con el mercado cerrado
   (mismo cierre, misma ventana) no crean filas duplicadas;
2. el «resultado real» que después se muestra en el dashboard se calcula
   desplazándose desde ese timestamp dentro de la propia serie, sin depender de
   cuándo corrió el proceso;
3. el índice único del esquema (`uq_ai_predictions_time`) lo garantiza en
   cualquier caso, incluso si dos procesos escribieran a la vez.

M1 **no** escribe aquí: solo M2 persiste (§15). La fila lleva lo que RF-303 y
RF-304 exigen auditar: tendencia, confianza y justificación XAI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from src.database import pg
from src.security.perfiles import TENDENCIAS_VALIDAS

_INSERTAR = """
    INSERT INTO ai_predictions
        (prediction_time, trend_prediction, confidence_score, technical_rationale)
    VALUES (%s, %s, %s, %s)
    ON CONFLICT (prediction_time) DO NOTHING
    RETURNING id
"""

_ULTIMA = """
    SELECT id, prediction_time, trend_prediction, confidence_score, technical_rationale
    FROM ai_predictions
    ORDER BY prediction_time DESC
    LIMIT 1
"""

_LISTAR = """
    SELECT id, prediction_time, trend_prediction, confidence_score, technical_rationale
    FROM ai_predictions
    ORDER BY prediction_time DESC
    LIMIT %s OFFSET %s
"""

_CONTAR = "SELECT count(*) FROM ai_predictions"


@dataclass(frozen=True)
class PrediccionNueva:
    """Lo que el ciclo de M2 intenta persistir."""

    prediction_time: int  #: epoch ms del cierre de la ÚLTIMA VELA de la ventana
    trend_prediction: str
    confidence_score: float
    technical_rationale: str


@dataclass(frozen=True)
class PrediccionGuardada:
    """Una fila leída de `ai_predictions`."""

    id: str
    prediction_time: int
    trend_prediction: str
    confidence_score: float
    technical_rationale: str


class RepositorioPredicciones(Protocol):
    """Interfaz que consumen el scheduler y el API (y que los tests falsifican)."""

    def guardar(self, pred: PrediccionNueva) -> PrediccionGuardada | None:
        """Inserta la fila y la devuelve; **None** si ya existía para esa vela."""
        ...

    def ultima(self) -> PrediccionGuardada | None: ...

    def listar(self, limit: int, offset: int) -> tuple[list[PrediccionGuardada], int]:
        """Página (orden descendente) y total de filas."""
        ...


def _validar(pred: PrediccionNueva) -> None:
    """Espejo de los CHECK del esquema: fallar aquí y no en medio de un commit."""
    if pred.trend_prediction not in TENDENCIAS_VALIDAS:
        raise ValueError(f"tendencia inválida: {pred.trend_prediction!r}")
    if not 0.0 <= pred.confidence_score <= 1.0:
        raise ValueError(f"confianza fuera de [0, 1]: {pred.confidence_score!r}")
    if not pred.technical_rationale.strip():
        raise ValueError("la justificación XAI no puede quedar vacía (RF-303)")


def _fila(tupla: tuple) -> PrediccionGuardada:
    identificador, tiempo, tendencia, confianza, rationale = tupla
    return PrediccionGuardada(
        id=str(identificador),
        prediction_time=int(tiempo),
        trend_prediction=str(tendencia),
        confidence_score=float(confianza),
        technical_rationale=str(rationale),
    )


class PrediccionesPostgres:
    """Implementación real sobre PostgreSQL (`src/database/schema.sql`)."""

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn

    def guardar(self, pred: PrediccionNueva) -> PrediccionGuardada | None:
        _validar(pred)
        with pg.conexion(self._dsn) as conn:
            with conn.cursor() as cur:
                # `RETURNING id`: con el conflicto descartado no vuelve fila y
                # `fetchone()` es None, que es cómo se distingue "insertada" de
                # "ya existía" sin una segunda consulta.
                cur.execute(
                    _INSERTAR,
                    (pred.prediction_time, pred.trend_prediction,
                     float(pred.confidence_score), pred.technical_rationale),
                )
                fila = cur.fetchone()
        if fila is None:
            return None
        return PrediccionGuardada(
            id=str(fila[0]),
            prediction_time=pred.prediction_time,
            trend_prediction=pred.trend_prediction,
            confidence_score=pred.confidence_score,
            technical_rationale=pred.technical_rationale,
        )

    def ultima(self) -> PrediccionGuardada | None:
        with pg.conexion(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(_ULTIMA)
                fila = cur.fetchone()
        return _fila(fila) if fila else None

    def listar(self, limit: int, offset: int) -> tuple[list[PrediccionGuardada], int]:
        with pg.conexion(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(_LISTAR, (limit, offset))
                filas = cur.fetchall()
                cur.execute(_CONTAR)
                total = int(cur.fetchone()[0])
        return [_fila(fila) for fila in filas], total
