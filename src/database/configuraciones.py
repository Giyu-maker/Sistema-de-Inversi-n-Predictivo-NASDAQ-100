"""`system_configurations` — configuración operativa que el API solo lee.

**Siembra idempotente (decisión D3).** La tabla existe vacía desde el Sprint 2:
nadie la llena porque no había escritor. Antes de que el dashboard muestre un
modo y un perfil, alguien tiene que poner la primera fila, y ese alguien es el
arranque del scheduler — no el API, que en esta fase emite únicamente `SELECT`.

Valores iniciales y su procedencia:

| campo | valor | procedencia |
|---|---|---|
| `max_loss_day` | 1,25 | SRS RF-202 / AGENTS.md §6 |
| `is_autonomous` | FALSE | RF-401: arranca en Modo Asesor |
| `risk_profile` | **MODERATE** | RF-304 y SDD §5.4 lo fijan por defecto |

El `DEFAULT 'CONVENTIONAL'` de la columna (SDD §3.1.1) **no** es el umbral por
defecto del sistema: MODERATE es 0,75, que es el número que citan RF-304 y
SDD §5.4. La discrepancia queda registrada en `docs/change-requests.md` CR-004;
aquí se escribe el valor explícitamente para que no dependa del DDL.

`sembrar_inicial()` solo inserta si la tabla está vacía, así que llamarlo en
cada arranque no duplica nada aunque corran scheduler y API a la vez (a la
segunda llamada la condición ya no se cumple).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from src.database import pg
from src.security.perfiles import PERFIL_POR_DEFECTO, umbral_de

#: Valores de arranque. Documentados arriba, con su procedencia.
MAX_LOSS_DAY_INICIAL = 1.25
MODO_INICIAL_AUTONOMO = False

_LEER = """
    SELECT max_loss_day, is_autonomous, risk_profile
    FROM system_configurations
    ORDER BY id
    LIMIT 1
"""

_SEMBRAR = """
    INSERT INTO system_configurations (max_loss_day, is_autonomous, risk_profile)
    SELECT %s, %s, %s
    WHERE NOT EXISTS (SELECT 1 FROM system_configurations)
"""


@dataclass(frozen=True)
class ConfiguracionSistema:
    """Fila de configuración con el umbral ya derivado del perfil activo."""

    max_loss_day: float
    is_autonomous: bool
    risk_profile: str

    @property
    def umbral_confianza(self) -> float:
        return umbral_de(self.risk_profile)


class RepositorioConfiguraciones(Protocol):
    def leer(self) -> ConfiguracionSistema | None:
        """None si nadie ha sembrado todavía la fila inicial."""
        ...

    def sembrar_inicial(self) -> bool:
        """True si esta llamada insertó la fila (False si ya existía)."""
        ...


class ConfiguracionesPostgres:
    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn

    def leer(self) -> ConfiguracionSistema | None:
        with pg.conexion(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(_LEER)
                fila = cur.fetchone()
        if not fila:
            return None
        max_loss, autonomo, perfil = fila
        return ConfiguracionSistema(
            max_loss_day=float(max_loss),
            is_autonomous=bool(autonomo),
            risk_profile=str(perfil),
        )

    def sembrar_inicial(self) -> bool:
        with pg.conexion(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    _SEMBRAR, (MAX_LOSS_DAY_INICIAL, MODO_INICIAL_AUTONOMO, PERFIL_POR_DEFECTO)
                )
                sembrada = cur.rowcount > 0
        return sembrada


def configuracion_por_defecto() -> ConfiguracionSistema:
    """Valores usados cuando aún no hay fila (API honesto, no inventado).

    Se exponen **con** un aviso en `/api/status`: el dashboard puede pintar el
    modo Asesor y el umbral 0,75, pero deja constancia de que vienen de los
    valores por defecto y no de la base de datos.
    """
    return ConfiguracionSistema(
        max_loss_day=MAX_LOSS_DAY_INICIAL,
        is_autonomous=MODO_INICIAL_AUTONOMO,
        risk_profile=PERFIL_POR_DEFECTO,
    )
