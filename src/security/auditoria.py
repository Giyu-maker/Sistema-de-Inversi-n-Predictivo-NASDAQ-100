"""Auditoría de acciones de negocio — la regla 3 del SDD §7, hecha código.

La regla
--------
SDD §7 regla 3: ninguna clase de negocio ejecuta trading ni cambia perfiles
de riesgo **sin insertar síncronamente** su log en `audit_logs`, incluyendo
la IP interna de la intranet. Este módulo es ese insert, y nada más.

Dos caminos, una sola política
------------------------------
* **Estricto** (`auditar_sincrono`): el log se inserta y **cualquier fallo se
  propaga**. Quien llama no ejecuta la acción sin su registro: sin auditoría
  no hay orden, ni cambio de modo, ni reinicio. Es la lectura literal de la
  regla 3 y la que usan el flujo de órdenes, el cambio de modo y el reinicio
  del Kill-Switch.
* **Mejor esfuerzo**: existe solo para el **Kill-Switch**, que por diseño no
  puede depender de PostgreSQL ni de la auditoría — un botón de pánico que
  esperara a la base de datos no sería un botón de pánico. Ese camino vive en
  `src/security/kill_switch.py` y, si la auditoría falla, vuelca el evento a
  `logs/auditoria-pendiente.jsonl` en lugar de abortar la acción.

Qué garantiza la base de datos
------------------------------
La inmutabilidad no es de esta capa: la imponen los triggers y el `REVOKE`
de `src/database/schema.sql` (UPDATE, DELETE y TRUNCATE rechazados, verificado
contra PostgreSQL real en `tests/test_schema.py`). Aquí solo se inserta.

Los actores
-----------
Sin autenticación de usuarios en esta fase (CR-006) no hay `user_id` que
registrar, pero sí quién actuó:

* `SISTEMA` — el scheduler y cualquier proceso sin persona detrás;
* `OPERADOR` — una persona que pulsa algo en el panel;
* `CLAVE_REINICIO` — el reinicio del Kill-Switch, que se autoriza con la
  clave de reinicio y no con una identidad personal.

El CHECK del esquema es el espejo de `ACTORES_VALIDOS`: fallar aquí ahorra un
viaje a la base para saber que el valor iba a ser rechazado.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from src.database import pg

#: Valores admitidos por `audit_logs.actor` (espejo del CHECK del esquema).
ACTORES_VALIDOS: tuple[str, ...] = ("SISTEMA", "OPERADOR", "CLAVE_REINICIO")

#: IP de los procesos sin persona detrás (scheduler, API). Es la IP del propio
#: equipo en la intranet: en M2 todo corre en la misma máquina.
IP_LOCAL = "127.0.0.1"

_INSERTAR = """
    INSERT INTO audit_logs (timestamp, user_id, action, actor, ip_address, payload)
    VALUES (%s, %s, %s, %s, %s, %s)
"""


class ErrorDeAuditoria(RuntimeError):
    """El log no se pudo insertar. Quien llama decide, pero NO en silencio."""


@dataclass(frozen=True)
class EventoAuditoria:
    """Un evento listo para `audit_logs`. Se valida al construirlo.

    Validar antes de tocar la base es lo mismo que hace `_validar` en
    `src/database/predictions.py`: mejor fallar en el caller que a mitad de un
    INSERT que ya abrió transacción.
    """

    action: str
    actor: str
    ip_address: str
    payload: Mapping[str, Any] | None = None
    user_id: str | None = None
    timestamp_ms: int | None = None  #: None = ahora (epoch ms)

    def __post_init__(self) -> None:
        if self.actor not in ACTORES_VALIDOS:
            validos = ", ".join(ACTORES_VALIDOS)
            raise ValueError(f"actor desconocido: {self.actor!r} (válidos: {validos})")
        if not self.action.strip():
            raise ValueError("la acción no puede quedar vacía")
        if len(self.action) > 100:
            # VARCHAR(100) en el esquema: mejor rechazar aquí que dejar que
            # PostgreSQL lo haga con un error de longitud opaco.
            raise ValueError(f"la acción no puede superar 100 caracteres ({len(self.action)})")
        if not self.ip_address.strip():
            # La IP es NOT NULL y es justamente lo que hace útil el registro
            # (AGENTS.md §5.3): sin ella no se prueba desde dónde se actuó.
            raise ValueError("la IP interna es obligatoria")
        if self.payload is not None:
            # Comprueba serializabilidad sin guardar nada: el JSON se arma en
            # `registrar`. `default=str` convierte lo exótico (Decimal,
            # datetime) en texto en lugar de romper una auditoría; lo que SÍ
            # se rechaza aquí es un payload mal formado: referencias
            # circulares o claves que no sean texto.
            json.dumps(self.payload, ensure_ascii=False, default=str)

    def payload_json(self) -> str | None:
        """Payload como texto JSON (la columna es TEXT)."""
        if self.payload is None:
            return None
        return json.dumps(self.payload, ensure_ascii=False, default=str, sort_keys=True)


class RepositorioAuditoria(Protocol):
    """Superficie que consumen el resto del sistema (y que los tests falsifican)."""

    def registrar(self, evento: EventoAuditoria) -> None:
        """Inserta el log de forma síncrona. **Propaga** cualquier fallo."""
        ...


def auditar_sincrono(repo: RepositorioAuditoria, evento: EventoAuditoria) -> None:
    """Inserta el log y deja que el fallo suba (SDD §7 regla 3).

    No hay reintento, ni `try/except`, ni log alternativo: si esto falla, la
    acción de negocio **no se ejecuta** y el error llega a quien la pidió. Es
    deliberado — un fallo silencioso de auditoría es exactamente lo que la
    regla 3 existe para impedir.

    Excepción única, ya declarada: el camino del Kill-Switch.
    """
    repo.registrar(evento)


class AuditoriaPostgres:
    """Implementación real sobre `audit_logs` (`src/database/schema.sql`)."""

    def __init__(
        self,
        dsn: str | None = None,
        ahora: Callable[[], float] | None = None,
    ) -> None:
        self._dsn = dsn
        #: Reloj inyectable para los tests; por defecto epoch en segundos.
        self._ahora = ahora if ahora is not None else time.time

    def registrar(self, evento: EventoAuditoria) -> None:
        timestamp_ms = evento.timestamp_ms
        if timestamp_ms is None:
            timestamp_ms = int(self._ahora() * 1000)
        try:
            with pg.conexion(self._dsn) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        _INSERTAR,
                        (
                            timestamp_ms,
                            evento.user_id,
                            evento.action,
                            evento.actor,
                            evento.ip_address,
                            evento.payload_json(),
                        ),
                    )
        except Exception as exc:  # noqa: BLE001 — traducir el fallo sin taparlo
            # Se envuelve para que el caller distinga "falló la auditoría" de
            # "falló su propia lógica", pero NUNCA se traga: la regla 3
            # exige que el error llegue arriba.
            raise ErrorDeAuditoria(
                f"no se pudo registrar la acción {evento.action!r}: {exc}"
            ) from exc
