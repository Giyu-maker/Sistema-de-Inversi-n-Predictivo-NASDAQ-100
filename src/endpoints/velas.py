"""`GET /api/candles` — velas de 5 minutos para el gráfico del dashboard.

Rango, límite y agregación, con la agregación resuelta **en Flux** (SDD §6.2):
`aggregate=5m` devuelve las velas tal cual se almacenaron; `15m`, `1h` y `1d`
las agregan por columna (apertura `first`, máximo `max`, mínimo `min`, cierre
`last`, volumen `sum`) dentro de InfluxDB, sin traer a Python más filas de las
que se piden.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from src.database.influx_lectura import LectorVelas
from src.endpoints.convertidores import a_vela_salida
from src.endpoints.dependencias import EstadoAplicacion, estado_app
from src.endpoints.esquemas import VelasSalida

router = APIRouter()

NOTA = (
    "Velas de 5 minutos del índice (proxy QQQ, RF-101). "
    "Las agregaciones superiores se calculan en InfluxDB."
)


def _lector(estado: EstadoAplicacion = Depends(estado_app)) -> LectorVelas:
    return estado.lector


@router.get("/api/candles", response_model=VelasSalida, tags=["mercado"])
def candles(
    start: int | None = Query(
        default=None, ge=0, description="epoch ms del inicio (inclusive)."
    ),
    stop: int | None = Query(default=None, ge=0, description="epoch ms del fin (exclusive)."),
    limit: int | None = Query(
        default=None, ge=1, le=5000,
        description="Máximo de velas; sin rango, por defecto 200.",
    ),
    aggregate: Literal["5m", "15m", "1h", "1d"] = "5m",
    lector: LectorVelas = Depends(_lector),
) -> VelasSalida:
    """Velas en rango o las últimas `limit`.

    Sin `start`/`stop` devuelve las últimas velas (orden ascendente). Con rango,
    devuelve ese tramo; `limit` opcional recorta desde el inicio del rango.
    """
    try:
        if start is None and stop is None:
            velas = lector.ultimas_velas(limit or 200)
        elif start is None or stop is None:
            raise HTTPException(
                status_code=422,
                detail="`start` y `stop` deben ir juntos: no se puede abrir un rango a medias.",
            )
        else:
            velas = lector.velas_rango(start, stop, agregacion=aggregate, limit=limit)
    except HTTPException:
        raise
    except ValueError as exc:
        # Parámetros de rango/agregación inválidos: 422 con el mensaje del
        # propio validador, que ya está escrito en español.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=f"Base de series temporales no disponible: {exc}"
        ) from exc

    return VelasSalida(
        velas=[a_vela_salida(vela) for vela in velas],
        total=len(velas),
        aggregate=aggregate,
        nota=NOTA,
    )
