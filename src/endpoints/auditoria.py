"""`GET /api/auditoria` — visor de solo lectura de `audit_logs` (RF-701).

**Solo lectura, sin excepción:** el módulo tiene una única ruta y es un
`GET`. No hay POST, PUT ni DELETE que añadir, y si alguien añade uno, el
test `test_no_hay_escrituras` lo hace tronar: la auditoría se escribe desde
el backend con `src/security/auditoria.py`, nunca desde la interfaz.

El rango usa el mismo criterio que los reportes (`resolver_rango`, de
`src/endpoints/reportes.py`): `AAAA-MM-DD` de sesión en ET y, sin fechas,
las últimas 5 sesiones. Dos rutas distintas con dos ideas distintas del
mismo rango sería una bomba de tiempo de concordancia.

Los filtros por `actor` y `accion` son de **coincidencia exacta**: son
valores de un catálogo corto (`SISTEMA`/`OPERADOR`/`CLAVE_REINICIO` y los
`action` del esquema), y un `LIKE` parcial haría que `KILL` devolviera lo
mismo que `KILL_SWITCH_TRIGGERED` sin que el operador lo esperara.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

from src.database.auditoria import ErrorDeAuditoriaLectura, FiltroAuditoria
from src.endpoints.dependencias import EstadoAplicacion, estado_app
from src.endpoints.esquemas import AuditoriaSalida, RegistroAuditoriaSalida
from src.endpoints.reportes import resolver_rango
from src.utils.fechas import fecha_hora
from src.utils.mercado import apertura_ms

logger = logging.getLogger(__name__)

router = APIRouter()

#: La nota la redacta el backend y la pinta la UI tal cual: la inmutabilidad
#: es una propiedad del sistema (RF-701), no un eslogan de la interfaz.
NOTA_INMUTABLE = "Registros inmutables a nivel de base de datos (RF-701)."


@router.get("/api/auditoria", response_model=AuditoriaSalida, tags=["auditoria"])
def auditoria(
    desde: str | None = None,
    hasta: str | None = None,
    actor: str | None = None,
    accion: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    estado: EstadoAplicacion = Depends(estado_app),
) -> AuditoriaSalida:
    """Página de registros con filtros, ordenados del más reciente al más antiguo."""
    dia_desde, dia_hasta = resolver_rango(estado, desde, hasta)

    if estado.auditoria is None:
        raise HTTPException(
            status_code=503,
            detail="El lector de auditoría no está configurado en este proceso.",
        )

    filtro = FiltroAuditoria(
        desde_ms=_inicio_del_dia(dia_desde),
        hasta_ms=_fin_del_dia(dia_hasta),
        actor=actor.strip() if actor and actor.strip() else None,
        accion=accion.strip() if accion and accion.strip() else None,
        limit=limit,
        offset=offset,
    )
    try:
        registros, total = estado.auditoria.listar(filtro)
    except ErrorDeAuditoriaLectura as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — cualquier fallo de I/O
        raise HTTPException(
            status_code=503, detail=f"Base de datos no disponible: {exc}"
        ) from exc

    return AuditoriaSalida(
        registros=[
            RegistroAuditoriaSalida(
                id=r.id,
                timestamp=r.timestamp,
                fecha=fecha_hora(r.timestamp),
                actor=r.actor,
                action=r.action,
                ip_address=r.ip_address,
                payload=r.payload,
            )
            for r in registros
        ],
        total=total,
        limit=limit,
        offset=offset,
        desde=dia_desde.isoformat(),
        hasta=dia_hasta.isoformat(),
        nota=NOTA_INMUTABLE,
    )


def _inicio_del_dia(dia) -> int:
    """09:30 ET del día (apertura), en epoch ms."""
    return apertura_ms(dia)


def _fin_del_dia(dia) -> int:
    """Un milisegundo antes de la apertura del día siguiente (exclusivo)."""
    return apertura_ms(dia + timedelta(days=1)) - 1
