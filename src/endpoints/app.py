"""Montaje de la API de M2: routers, manejadores y estado.

`crear_app()` es la única puerta: recibe el estado (los tests inyectan fakes) o
lo deja para que `estado_app` lo construya desde el entorno en la primera
petición. Eso deja el import del módulo libre de leer `.env` ni abrir clientes,
que es lo que permite probar la app completa sin tocar servicios reales.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from src.endpoints import calidad, estado, predicciones, rendimiento, salud, velas
from src.endpoints.dependencias import EstadoAplicacion
from src.endpoints.errores import instalar_manejadores

logger = logging.getLogger(__name__)

TITULO = "RiTech — API de solo lectura (M2)"
VERSION = "0.1.0"
DESCRIPCION = (
    "Consulta de estado, velas, rendimiento virtual y predicciones del sistema "
    "predictivo NASDAQ-100 (Sprint 3, Fase B). SOLO GET: no escribe en ninguna "
    "base de datos ni envía órdenes. Sin autenticación en esta fase "
    "(RNF-001/RNF-005 → Sprint 4)."
)


def crear_app(estado_inicial: EstadoAplicacion | None = None) -> FastAPI:
    """API completa con sus seis ruteadores y sus manejadores en español.

    El parámetro no se llama `estado` porque ese nombre ya es el módulo
    `src.endpoints.estado`: sombrearlo aquí compilaba la app sin su ruteador de
    estado en cada arranque.
    """
    app = FastAPI(title=TITULO, version=VERSION, description=DESCRIPCION)
    instalar_manejadores(app)
    for paquete in (salud, estado, velas, predicciones, rendimiento, calidad):
        app.include_router(paquete.router)
    # None = "construir desde el entorno al primer request" (ver estado_app).
    app.state.riTech = estado_inicial
    return app


#: Para `uvicorn src.endpoints.app:app` y para el healthcheck del contenedor.
app = crear_app()
