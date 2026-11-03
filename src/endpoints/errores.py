"""Errores de la API — todos los mensajes que devuelve van en español neutro.

FastAPI y Pydantic hablan inglés por defecto (`"Input should be less than or
equal to 500"`), y el SRS exige mensajes de error claros en español para el
operador (AGENTS.md §5.5). Este módulo traduce en dos niveles:

* **Validación (422)** — recorre los errores de campo y traduce el tipo de
  error; el campo se conserva tal cual (`query.limit`) porque es el nombre del
  contrato y traducirlo lo volvería irreconocible.
* **Dominio (503/500)** — `ErrorDeInferencia` y `ErrorDeBaseDatos` son fallos
  de dependencias localizadas: se contesta con el estado que corresponde y un
  mensaje utilizable, nunca con el stack.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.ai.client import ErrorDeInferencia
from src.database.pg import ErrorDeBaseDatos
from src.security.auditoria import ErrorDeAuditoria

logger = logging.getLogger(__name__)

#: tipo de error Pydantic → plantilla en español (`{...}` = contexto del error).
_MENSAJES: dict[str, str] = {
    "missing": "es obligatorio",
    "greater_than_equal": "debe ser mayor o igual a {ge}",
    "greater_than": "debe ser mayor que {gt}",
    "less_than_equal": "debe ser menor o igual a {le}",
    "less_than": "debe ser menor que {lt}",
    "int_parsing": "debe ser un número entero",
    "int_type": "debe ser un número entero",
    "float_parsing": "debe ser un número",
    "float_type": "debe ser un número",
    "string_type": "debe ser texto",
    "string_too_short": "es demasiado corto",
    "string_too_long": "es demasiado largo",
    "bool_parsing": "debe ser verdadero o falso",
    "literal_error": "no es un valor permitido",
    "extra_forbidden": "no es un campo permitido",
    "model_attributes_type": "no es un objeto válido",
    "json_invalid": "no es JSON válido",
}


def _mensaje_de(error: dict) -> str:
    """Un error de validación Pydantic → frase en español."""
    tipo = str(error.get("type", ""))
    plantilla = _MENSAJES.get(tipo)
    if plantilla is None:
        # Sin traducción conocida: se informa el campo y el tipo, que es
        # información suficiente para arreglar la petición sin exponer texto
        # en inglés en una respuesta para el operador.
        return f"no es válido ({tipo or 'valor'})"
    contexto = {k: v for k, v in (error.get("ctx") or {}).items()}
    try:
        return plantilla.format(**contexto)
    except (KeyError, IndexError):  # plantilla con contexto inesperado
        return plantilla.split("{")[0].strip()


def _detalle_validacion(exc: RequestValidationError) -> list[dict[str, str]]:
    """Igual que en `src/ai/serve.py`, traducido y sin ecoar el cuerpo.

    No se devuelve `input`: una petición traumada (una ventana entera, por
    ejemplo) no debe aparecer dentro de la respuesta de error.
    """
    detalles: list[dict[str, str]] = []
    for error in exc.errors():
        detalles.append(
            {
                "campo": ".".join(str(parte) for parte in error.get("loc", ())),
                "mensaje": _mensaje_de(error),
            }
        )
    return detalles


def instalar_manejadores(app: FastAPI) -> None:
    """Registra los manejadores de error del API."""

    @app.exception_handler(RequestValidationError)
    async def _validacion(request: Request, exc: RequestValidationError) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=422,
            content={"detail": _detalle_validacion(exc), "mensaje": "Petición no válida."},
        )

    @app.exception_handler(ErrorDeInferencia)
    async def _inferencia(request: Request, exc: ErrorDeInferencia) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=503,
            content={
                "mensaje": "La IA no responde. Consulte al administrador de TI.",
                "detalle": str(exc),
            },
        )

    @app.exception_handler(ErrorDeBaseDatos)
    async def _base_datos(request: Request, exc: ErrorDeBaseDatos) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=503,
            content={"mensaje": "Base de datos no disponible.", "detalle": str(exc)},
        )

    @app.exception_handler(ErrorDeAuditoria)
    async def _auditoria(request: Request, exc: ErrorDeAuditoria) -> JSONResponse:
        """Sin auditoría no hay acción (SDD §7 regla 3): se contesta y no se ejecuta.

        Aparece en el reinicio del Kill-Switch y en cualquier acción estricta.
        El disparo NO pasa por aquí: ese camino es deliberadamente tolerante a
        fallos y su respaldo es `logs/auditoria-pendiente.jsonl`.
        """
        del request
        return JSONResponse(
            status_code=503,
            content={
                "mensaje": "No se pudo registrar la acción en la auditoría. No se ejecutó.",
                "detalle": str(exc),
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """Las 404/405 propias también salen en español."""
        del request
        mensajes = {404: "Recurso no encontrado.", 405: "Método no permitido."}
        return JSONResponse(
            status_code=exc.status_code,
            content={"mensaje": mensajes.get(exc.status_code, str(exc.detail))},
        )

    @app.exception_handler(Exception)
    async def _inesperado(request: Request, exc: Exception) -> JSONResponse:
        # El traceback va al log del servidor, no a la respuesta: allí sí sirve
        # para diagnosticar, en la respuesta solo filtra internos.
        logger.exception("Error no contemplado atendiendo %s", request.url.path)
        return JSONResponse(
            status_code=500,
            content={"mensaje": "Error interno del servidor. Revise los registros."},
        )
