"""Lectura de `audit_logs` — el visor y los reportes (RF-701, RF-702).

Acá **solo se lee**. La inmutabilidad la imponen los triggers de
`src/database/schema.sql` (UPDATE, DELETE y TRUNCATE rechazados) y este
módulo no tiene una sola sentencia que no sea `SELECT`: es la garantía
visible de que el visor no puede alterar lo que muestra.

No confundir con `src/security/auditoria.py`, que es la capa de
**escritura** (el insert síncrono de la regla 3 del SDD §7). Este archivo
existe porque RF-701 pide conservar los registros y RF-801/RF-702 piden
poder leerlos: dos necesidades distintas, dos módulos distintos.

Los filtros llegan como `FiltroAuditoria` y se traducen a un `WHERE`
parametrizado — nunca por interpolación: `actor` y `accion` son valores
que alguien escribe en una query string.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from src.database import pg


class ErrorDeAuditoriaLectura(RuntimeError):
    """La consulta de auditoría falló; el endpoint la traduce a 503."""


@dataclass(frozen=True)
class RegistroAuditoria:
    """Una fila de `audit_logs`, tal cual está guardada."""

    id: str
    timestamp: int  #: epoch ms
    actor: str
    action: str
    ip_address: str
    payload: str | None  #: JSON como texto; la UI lo formatea
    user_id: str | None


@dataclass(frozen=True)
class FiltroAuditoria:
    """Rango obligatorio + filtros opcionales, con paginación."""

    desde_ms: int
    hasta_ms: int  #: exclusivo
    actor: str | None = None
    accion: str | None = None
    #: Varias acciones a la vez (el reporte de Kill-Switch pide
    #: `KILL_SWITCH_TRIGGERED` y `KILL_SWITCH_RESET` en la misma consulta).
    acciones: tuple[str, ...] | None = None
    limit: int = 50
    offset: int = 0


@dataclass(frozen=True)
class ResumenAuditoria:
    """Agrupación acción × actor para el reporte de resumen."""

    accion: str
    actor: str
    registros: int
    primera: int  #: epoch ms del más antiguo
    ultima: int  #: epoch ms del más reciente


class LectorAuditoria(Protocol):
    """Superficie que consumen `GET /api/auditoria` y los reportes."""

    def listar(self, filtro: FiltroAuditoria) -> tuple[list[RegistroAuditoria], int]:
        """Página (más reciente primero) y total que cumple el filtro."""
        ...

    def resumen(self, desde_ms: int, hasta_ms: int) -> list[ResumenAuditoria]:
        """Conteos por acción y actor dentro del rango."""
        ...


def _condiciones(filtro: FiltroAuditoria) -> tuple[str, list[Any]]:
    """`WHERE` parametrizado: el rango siempre, el resto si llegó."""
    condiciones = ["timestamp >= %s", "timestamp < %s"]
    params: list[Any] = [filtro.desde_ms, filtro.hasta_ms]
    if filtro.actor:
        condiciones.append("actor = %s")
        params.append(filtro.actor)
    if filtro.accion:
        condiciones.append("action = %s")
        params.append(filtro.accion)
    if filtro.acciones:
        # `IN` con los marcadores que hagan falta: también parametrizado,
        # nunca interpolando los valores.
        marcadores = ", ".join("%s" for _ in filtro.acciones)
        condiciones.append(f"action IN ({marcadores})")
        params.extend(filtro.acciones)
    return " AND ".join(condiciones), params


_LISTAR = """
    SELECT id, timestamp, actor, action, ip_address, payload, user_id
    FROM audit_logs
    WHERE {where}
    ORDER BY timestamp DESC, id
    LIMIT %s OFFSET %s
"""

_CONTAR = "SELECT count(*) FROM audit_logs WHERE {where}"

_RESUMEN = """
    SELECT action, actor, count(*), min(timestamp), max(timestamp)
    FROM audit_logs
    WHERE timestamp >= %s AND timestamp < %s
    GROUP BY action, actor
    ORDER BY action, actor
"""


class LectorAuditoriaPostgres:
    """Implementación real sobre `audit_logs` (solo SELECT)."""

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn

    def listar(self, filtro: FiltroAuditoria) -> tuple[list[RegistroAuditoria], int]:
        where, params = _condiciones(filtro)
        try:
            with pg.conexion(self._dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute(_LISTAR.format(where=where), (*params, filtro.limit, filtro.offset))
                    filas = cur.fetchall()
                    cur.execute(_CONTAR.format(where=where), params)
                    total = int(cur.fetchone()[0])
        except Exception as exc:  # noqa: BLE001 — traducido a 503 en el endpoint
            raise ErrorDeAuditoriaLectura(f"no se pudo leer la auditoría: {exc}") from exc
        return [_a_registro(f) for f in filas], total

    def resumen(self, desde_ms: int, hasta_ms: int) -> list[ResumenAuditoria]:
        try:
            with pg.conexion(self._dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute(_RESUMEN, (desde_ms, hasta_ms))
                    filas = cur.fetchall()
        except Exception as exc:  # noqa: BLE001
            raise ErrorDeAuditoriaLectura(f"no se pudo resumir la auditoría: {exc}") from exc
        return [
            ResumenAuditoria(
                accion=str(accion),
                actor=str(actor),
                registros=int(registros),
                primera=int(primera),
                ultima=int(ultima),
            )
            for accion, actor, registros, primera, ultima in filas
        ]


def _a_registro(tupla: tuple) -> RegistroAuditoria:
    identificador, timestamp, actor, accion, ip, payload, usuario = tupla
    return RegistroAuditoria(
        id=str(identificador),
        timestamp=int(timestamp),
        actor=str(actor),
        action=str(accion),
        ip_address=str(ip),
        payload=str(payload) if payload is not None else None,
        user_id=str(usuario) if usuario is not None else None,
    )
