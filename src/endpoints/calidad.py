"""`GET /api/model-quality` — nota permanente de calidad del modelo.

La interfaz debe mostrar **siempre** cuándo se entrenó el modelo, su macro-F1 y
su acierto direccional, junto con la advertencia de que no se demostró ventaja
direccional (informe Sprint 3 §10.3). Este endpoint expone esos números; no
reentrena ni los recalcula: son los del artefacto que sirve M1.

Si M1 no responde la respuesta **no es un error HTTP**: es un estado resuelto
(`disponible=false`) con el mismo formato. La UI puede pintar «calidad no
disponible» sin cambiar de pantalla ni inventar cifras.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from src.endpoints.dependencias import (
    EstadoAplicacion,
    consultar_calidad,
    estado_app,
)
from src.endpoints.esquemas import CalidadModeloSalida

router = APIRouter()


@router.get("/api/model-quality", response_model=CalidadModeloSalida, tags=["modelo"])
def model_quality(estado: EstadoAplicacion = Depends(estado_app)) -> CalidadModeloSalida:
    """Métricas del artefacto activo, con caché corta (`cache_calidad_s`)."""
    return consultar_calidad(estado)
