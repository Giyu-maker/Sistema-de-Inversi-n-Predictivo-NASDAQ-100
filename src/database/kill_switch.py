"""`kill_switch_state` — el estado que sobrevive a un reinicio (RF-501, CR-005).

Por qué en la base de datos y no en memoria
-------------------------------------------
La bandera de `src/security/kill_switch.py` es la que corta **antes que nada**
(RNF-010), pero es de un solo proceso: si el servicio se reinicia, la memoria
se vacía y el sistema quedaría operable sin que nadie lo decidiera. La fila de
aquí es la verdad compartida entre el API y el scheduler, y es la que
`RiskManager` consulta antes de emitir cualquier orden.

Compare-and-set, no leer-y-escribir
-----------------------------------
`activar()` hace `UPDATE ... WHERE activo = FALSE RETURNING`: si devuelve fila,
esta llamada fue la que activó; si no, alguien ya había activado. Ese es el
mecanismo de idempotencia —pulsar dos veces no liquidó dos veces— y funciona
aunque dos procesos pulsen a la vez, cosa que un leer-y-escribir no
garantizaría.

Lectura que falla cerrado
-------------------------
`leer()` ordena `activo DESC`: si por una carrera existieran dos filas, gana la
que está activa. Y si no hay fila (nunca se sembró, o PostgreSQL no responde),
devuelve `None` — el caller decide, y la regla del Sprint 4 es no emitir
órdenes cuando el estado no se puede leer.

Singleton por convención, como `system_configurations`: la fila la siembra
Python al arrancar (`sembrar_si_vacio`), no el DDL, para no romper la regla
estática de `tests/test_schema.py` (el esquema no fabrica datos).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from src.database import pg

#: Origen de la activación (columna `origen`).
ORIGENES: tuple[str, ...] = ("MANUAL", "AUTOMATICO")

#: Quién pudo activar (columna `actor`). El reinicio se audita aparte.
ACTORES_ACTIVACION: tuple[str, ...] = ("SISTEMA", "OPERADOR")

#: Estados de la liquidación (columna `liquidacion_estado`).
ESTADOS_LIQUIDACION: tuple[str, ...] = (
    "NINGUNA", "PENDIENTE", "EN_CURSO", "COMPLETADA", "FALLIDA",
)

_SEMBRAR = """
    INSERT INTO kill_switch_state (activo, motivo, origen, actor, disparado_en)
    SELECT FALSE, %s, %s, %s, 0
    WHERE NOT EXISTS (SELECT 1 FROM kill_switch_state)
"""

# `activo DESC` primero: si hubiera dos filas, manda la activa (falla cerrado).
_LEER = """
    SELECT activo, motivo, origen, actor, disparado_en, liquidacion_estado,
           liquidacion_en, liquidaciones, reinicio_en, sesion_reinicio,
           intentos_reinicio, ultimo_intento_en
    FROM kill_switch_state
    ORDER BY activo DESC, disparado_en DESC, id
    LIMIT 1
"""

# Compare-and-set: solo pasa de FALSE a TRUE, y solo una llamada gana.
_ACTIVAR = """
    UPDATE kill_switch_state
       SET activo = TRUE, motivo = %s, origen = %s, actor = %s, disparado_en = %s,
           liquidacion_estado = 'PENDIENTE', liquidacion_en = NULL,
           intentos_reinicio = 0, ultimo_intento_en = 0
     WHERE activo = FALSE
 RETURNING id
"""

_LIQUIDACION = """
    UPDATE kill_switch_state
       SET liquidacion_estado = %s, liquidacion_en = %s,
           liquidaciones = liquidaciones + %s
     WHERE activo = TRUE
"""

_INTENTO = """
    UPDATE kill_switch_state
       SET intentos_reinicio = intentos_reinicio + 1, ultimo_intento_en = %s
     WHERE activo = TRUE
 RETURNING intentos_reinicio
"""

_REINICIAR = """
    UPDATE kill_switch_state
       SET activo = FALSE, reinicio_en = %s, sesion_reinicio = %s,
           intentos_reinicio = 0, ultimo_intento_en = 0,
           liquidacion_estado = 'NINGUNA', liquidacion_en = NULL
     WHERE activo = TRUE
 RETURNING id
"""


@dataclass(frozen=True)
class EstadoKillSwitch:
    """Fila de `kill_switch_state`.

    `motivo`, `origen`, `actor` y `disparado_en` describen la **última**
    activación y no se borran al reiniciar: el reinicio deja su propia
    traza en `audit_logs` (actor CLAVE_REINICIO) y aquí lo que importa al
    leer es `activo`.
    """

    activo: bool
    motivo: str
    origen: str
    actor: str
    disparado_en: int
    liquidacion_estado: str
    liquidacion_en: int | None
    liquidaciones: int
    reinicio_en: int | None
    sesion_reinicio: date | None
    intentos_reinicio: int
    ultimo_intento_en: int


class RepositorioKillSwitch(Protocol):
    """Interfaz que consumen el motor, el API y el scheduler."""

    def sembrar_si_vacio(self) -> bool:
        """True si esta llamada creó la fila (False si ya existía)."""
        ...

    def leer(self) -> EstadoKillSwitch | None:
        """Estado vigente; **None** si no se pudo determinar (falla cerrado)."""
        ...

    def activar(self, *, motivo: str, origen: str, actor: str, ahora_ms: int) -> bool:
        """Compare-and-set. True solo si ESTA llamada pasó de inactivo a activo."""
        ...

    def marcar_liquidacion(self, estado: str, ahora_ms: int | None) -> None:
        """PENDIENTE → EN_CURSO → COMPLETADA/FALLIDA; suma al acumulado al completar."""
        ...

    def registrar_intento_fallido(self, ahora_ms: int) -> int | None:
        """+1 al contador de intentos de reinicio. None si no está activo."""
        ...

    def reiniciar(self, *, ahora_ms: int, sesion: date) -> bool:
        """Pasa a inactivo. True solo si estaba activo (idempotente)."""
        ...


class KillSwitchPostgres:
    """Implementación real sobre `kill_switch_state`."""

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn

    def sembrar_si_vacio(self) -> bool:
        with pg.conexion(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(_SEMBRAR, ("Sin disparo", "MANUAL", "SISTEMA"))
                sembrada = cur.rowcount > 0
        return sembrada

    def leer(self) -> EstadoKillSwitch | None:
        with pg.conexion(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(_LEER)
                fila = cur.fetchone()
        if not fila:
            return None
        (
            activo, motivo, origen, actor, disparado_en, liquidacion,
            liquidacion_en, liquidaciones, reinicio_en, sesion_reinicio,
            intentos, ultimo_intento,
        ) = fila
        return EstadoKillSwitch(
            activo=bool(activo),
            motivo=str(motivo),
            origen=str(origen),
            actor=str(actor),
            disparado_en=int(disparado_en),
            liquidacion_estado=str(liquidacion),
            liquidacion_en=int(liquidacion_en) if liquidacion_en is not None else None,
            liquidaciones=int(liquidaciones),
            reinicio_en=int(reinicio_en) if reinicio_en is not None else None,
            sesion_reinicio=sesion_reinicio,
            intentos_reinicio=int(intentos),
            ultimo_intento_en=int(ultimo_intento),
        )

    def activar(self, *, motivo: str, origen: str, actor: str, ahora_ms: int) -> bool:
        if origen not in ORIGENES:
            raise ValueError(f"origen desconocido: {origen!r}")
        if actor not in ACTORES_ACTIVACION:
            raise ValueError(f"actor de activación desconocido: {actor!r}")
        with pg.conexion(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(_ACTIVAR, (motivo, origen, actor, ahora_ms))
                ganada = cur.fetchone() is not None
        return ganada

    def marcar_liquidacion(self, estado: str, ahora_ms: int | None) -> None:
        if estado not in ESTADOS_LIQUIDACION:
            raise ValueError(f"estado de liquidación desconocido: {estado!r}")
        terminal = estado in ("COMPLETADA", "FALLIDA")
        with pg.conexion(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    _LIQUIDACION,
                    (estado, ahora_ms if terminal else None, 1 if estado == "COMPLETADA" else 0),
                )

    def registrar_intento_fallido(self, ahora_ms: int) -> int | None:
        with pg.conexion(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(_INTENTO, (ahora_ms,))
                fila = cur.fetchone()
        return int(fila[0]) if fila else None

    def reiniciar(self, *, ahora_ms: int, sesion: date) -> bool:
        with pg.conexion(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(_REINICIAR, (ahora_ms, sesion))
                reiniciada = cur.fetchone() is not None
        return reiniciada
