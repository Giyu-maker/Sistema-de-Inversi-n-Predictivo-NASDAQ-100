"""`GET /api/predictions` — historial de predicciones con su resultado real.

Dos endpoints:

* `/api/predictions/latest` — la última predicción + su veredicto de señal
  operable (`src/security/perfiles.py`, decisión D7: la regla vive solo en el
  backend y la UI la pinta).
* `/api/predictions` — página del historial, orden descendente.

**Resultado real en lote.** Un historial de 20 filas no puede hacer 20 consultas
a InfluxDB: se pide **un** rango que cubra desde la sesión de la predicción más
antigua de la página hasta el horizonte de la más nueva, y el contraste se
calcula en memoria sobre ese rango (SDD §6.2 solo exige que la **agregación**
sea en base de datos; aquí no se agrega nada, se evalúa).

Si InfluxDB no responde, el historial **se sirve igual** (viene de PostgreSQL) y
cada `resultado_real` queda null con un aviso: una columna vacía es preferible a
una cifra inventada.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from src.ai.features import Candle
from src.ai.prediccion import a_candle
from src.database.predictions import PrediccionGuardada
from src.endpoints.convertidores import a_prediccion_salida, a_senal_salida
from src.endpoints.dependencias import (
    EstadoAplicacion,
    configuracion_activa,
    senal_de,
    estado_app,
)
from src.endpoints.esquemas import PrediccionPaginaSalida, PrediccionUltimaSalida
from src.ingestion.engine import NasdaqCandle
from src.security.perfiles import senal_sin_prediccion
from src.utils.resultados import HORIZON_MS, ResultadoReal, resultado_real

logger = logging.getLogger(__name__)

router = APIRouter()

#: Margen para reconstruir la sesión de cada predicción: una sesión son 6 h 30 y
#: el horizonte necesita ver hasta 15 min después del cierre de la ventana.
MARGEN_RANGO_MS = 12 * 60 * 60 * 1000


@router.get(
    "/api/predictions/latest",
    response_model=PrediccionUltimaSalida,
    tags=["predicciones"],
)
def prediccion_mas_reciente(
    estado: EstadoAplicacion = Depends(estado_app),
) -> PrediccionUltimaSalida:
    """La última predicción persistida, con su señal y su resultado real."""
    vigente = configuracion_activa(estado)
    avisos = list(vigente.avisos)
    prediccion = _ultima(estado)

    if prediccion is None:
        return PrediccionUltimaSalida(
            encontrada=False,
            prediccion=None,
            senal=a_senal_salida(
                senal_sin_prediccion(vigente.configuracion.risk_profile)
            ),
            avisos=avisos,
        )

    resultado = _resultado_de_unico(estado, prediccion, avisos)
    return PrediccionUltimaSalida(
        encontrada=True,
        prediccion=a_prediccion_salida(prediccion, resultado),
        senal=a_senal_salida(
            senal_de(prediccion, vigente.configuracion.risk_profile, avisos)
        ),
        avisos=avisos,
    )


@router.get(
    "/api/predictions",
    response_model=PrediccionPaginaSalida,
    tags=["predicciones"],
)
def predicciones(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    estado: EstadoAplicacion = Depends(estado_app),
) -> PrediccionPaginaSalida:
    """Página del historial (más recientes primero) con resultado real por fila."""
    try:
        filas, total = estado.predicciones.listar(limit, offset)
    except Exception as exc:  # noqa: BLE001 — PostgreSQL caído → 503 en español
        raise HTTPException(
            status_code=503, detail=f"Base de datos no disponible: {exc}"
        ) from exc

    avisos: list[str] = []
    resultados = resultados_en_lote(filas, estado, avisos)
    return PrediccionPaginaSalida(
        predicciones=[
            a_prediccion_salida(fila, resultados.get(fila.prediction_time))
            for fila in filas
        ],
        total=total,
        limit=limit,
        offset=offset,
        avisos=avisos,
    )


def _ultima(estado: EstadoAplicacion) -> PrediccionGuardada | None:
    try:
        return estado.predicciones.ultima()
    except Exception as exc:  # noqa: BLE001 — PostgreSQL caído → 503 en español
        raise HTTPException(
            status_code=503, detail=f"Base de datos no disponible: {exc}"
        ) from exc


def _resultado_de_unico(
    estado: EstadoAplicacion, prediccion: PrediccionGuardada, avisos: list[str]
) -> ResultadoReal | None:
    """Resultado real de una sola predicción (una consulta, un rango corto)."""
    try:
        ultima_vela = estado.lector.ultima_vela()
        velas = estado.lector.velas_rango(
            prediccion.prediction_time - MARGEN_RANGO_MS,
            prediccion.prediction_time + HORIZON_MS + MARGEN_RANGO_MS,
        )
    except Exception as exc:  # noqa: BLE001 — InfluxDB no disponible
        avisos.append(
            f"No se pudieron leer los precios históricos: el resultado real no se "
            f"pudo calcular ({exc})."
        )
        return None
    return resultado_real(
        prediccion.prediction_time,
        _a_candles(velas),
        banda_pct=estado.banda_stable_pct,
        ultima_vela_ms=ultima_vela.timestamp if ultima_vela else None,
    )


def resultados_en_lote(
    filas: list[PrediccionGuardada], estado: EstadoAplicacion, avisos: list[str]
) -> dict[int, ResultadoReal]:
    """Una sola consulta a InfluxDB para toda la página.

    Pública porque el endpoint de reportes (RF-702) calcula el resultado real
    de la misma manera: una sola implementación del contraste predicción vs.
    precio, no dos que puedan discrepar.
    """
    if not filas:
        return {}
    try:
        ultima_vela = estado.lector.ultima_vela()
        inicio = min(f.prediction_time for f in filas) - MARGEN_RANGO_MS
        parada = max(f.prediction_time for f in filas) + HORIZON_MS + MARGEN_RANGO_MS
        velas = estado.lector.velas_rango(inicio, parada)
    except Exception as exc:  # noqa: BLE001 — se pagina sin resultado real
        logger.warning("No se pudo calcular el resultado real del historial: %s", exc)
        avisos.append(
            f"No se pudieron leer los precios históricos: el resultado real no está "
            f"en esta página ({exc})."
        )
        return {}

    convertidas = _a_candles(velas)
    ultima_ms = ultima_vela.timestamp if ultima_vela else None
    return {
        fila.prediction_time: resultado_real(
            fila.prediction_time,
            convertidas,
            banda_pct=estado.banda_stable_pct,
            ultima_vela_ms=ultima_ms,
        )
        for fila in filas
    }


def _a_candles(velas: list[NasdaqCandle]) -> list[Candle]:
    return [a_candle(vela) for vela in velas]
