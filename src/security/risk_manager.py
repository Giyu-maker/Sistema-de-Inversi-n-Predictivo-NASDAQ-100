"""`RiskManager` — la única puerta entre una predicción y una orden (SDD §5.4).

Qué decide y en qué orden
-------------------------
`decidir()` responde «¿esto se puede operar?» y devuelve la respuesta **ya
decidida**, en lenguaje llano. El orden no es decorativo:

1. **primero** el Kill-Switch: si está activo, o si su estado no se pudo
   verificar, se responde BLOQUEADO y no se calcula nada más (RNF-010: el
   bloqueo va antes que cualquier cálculo);
2. después la señal (`src/security/perfiles.py`, la ÚNICA regla, D7):
   `STABLE` nunca abre y la confianza tiene que alcanzar el umbral del perfil;
3. al final, el modo: Asesor → ALERTA (recomienda, no opera), Autónomo →
   ORDEN (siempre simulada en esta fase).

`STABLE` nunca llega a producir una orden, con la confianza que sea: es el
comportamiento correcto que el backlog del Ítem 9 manda conservar.

Qué NO decide este módulo
-------------------------
Nada se envía aquí. `decidir()` es puro: quien llama —el scheduler, el API,
los tests de integración— es el que decide si manda la orden al
`BrokerGateway`. Así la política se prueba sin red y sin base de datos.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from src.integration.broker import NuevaOrden
from src.security.kill_switch import KillSwitchEngine, ResultadoDisparo
from src.security.perfiles import evaluar_senal, umbral_de

logger = logging.getLogger(__name__)

#: Resultados posibles de `decidir()`, en orden de gravedad.
ACCIONES: tuple[str, ...] = ("BLOQUEADO", "ORDEN", "ALERTA", "SIN_SENAL")

TipoDecision = Literal["BLOQUEADO", "ORDEN", "ALERTA", "SIN_SENAL"]

#: Cantidad de la orden simulada.
#:
#: **Provisional y documentado como brecha**: el SRS no define dimensionamiento
#: de posición (RF-403 cambia umbrales de confianza, no tamaños), así que se
#: opera una unidad mientras no exista una regla aprobada por la Gerencia.
CANTIDAD_POR_DEFECTO = 1.0

#: Textos del modo, en lenguaje llano (AGENTS.md §5.5).
MOTIVO_ASESOR = (
    "Modo Asesor: el sistema solo recomienda y no emite órdenes. "
    "Un operador debe ejecutar la acción manualmente."
)
MOTIVO_AUTONOMO = "Modo Autónomo: orden simulada preparada para envío."


@dataclass(frozen=True)
class Decision:
    """Veredicto de la puerta de órdenes, listo para la UI y para el log."""

    accion: TipoDecision
    motivo: str
    tendencia: str | None
    confianza: float | None
    umbral: float
    perfil: str
    orden: NuevaOrden | None = None

    @property
    def es_orden(self) -> bool:
        return self.accion == "ORDEN" and self.orden is not None


class RiskManager:
    """Valida umbrales por perfil y administra el Kill-Switch (SDD §5.4)."""

    def __init__(self, motor: KillSwitchEngine, *, cantidad: float = CANTIDAD_POR_DEFECTO):
        self._motor = motor
        self._cantidad = cantidad

    # -- API de SDD §5.4 ---------------------------------------------------

    @staticmethod
    def evaluate_order_risk(prediction_confidence: float, active_profile: str) -> bool:
        """¿La confianza alcanza para el perfil activo? (SDD §5.4 literal).

        Delega en `umbral_de` en lugar del `.get(perfil, 0.75)` del SDD: un
        perfil desconocido debe gritar, no caer en silencio al umbral por
        defecto. La diferencia queda registrada en CR-006.
        """
        return prediction_confidence >= umbral_de(active_profile)

    def trigger_emergency_kill(
        self, *, actor: str, ip_address: str, motivo: str, origen: str = "MANUAL"
    ) -> ResultadoDisparo:
        """Dispara el Kill-Switch (SDD §5.4 `trigger_emergency_kill`).

        El `user_id` del SDD se sustituye por `actor`: en esta fase no existe
        autenticación de usuarios (CR-006) y el actor es SISTEMA u OPERADOR.
        """
        return self._motor.execute_global_liquidation(
            motivo=motivo, origen=origen, actor=actor, ip_address=ip_address
        )

    # -- la puerta ---------------------------------------------------------

    def decidir(
        self,
        *,
        tendencia: str,
        confianza: float,
        perfil: str,
        autonomo: bool,
        prediction_id: str | None = None,
    ) -> Decision:
        """Aplica la política completa, en el orden aprobado."""
        # 1) Kill-Switch ANTES que cualquier cálculo (RNF-010).
        resumen = self._motor.resumen()
        if resumen.activo is None:
            return Decision(
                accion="BLOQUEADO",
                motivo=resumen.detalle,
                tendencia=tendencia,
                confianza=confianza,
                umbral=_umbral_o_cero(perfil),
                perfil=perfil,
            )
        if resumen.activo:
            return Decision(
                accion="BLOQUEADO",
                motivo=resumen.detalle,
                tendencia=tendencia,
                confianza=confianza,
                umbral=_umbral_o_cero(perfil),
                perfil=perfil,
            )

        # 2) La señal, con la única regla del sistema (D7).
        try:
            senal = evaluar_senal(tendencia, confianza, perfil)
        except ValueError as exc:
            # Un dato corrupto no puede producir una orden ni una alerta.
            return Decision(
                accion="SIN_SENAL",
                motivo=f"No se pudo evaluar la señal: {exc}",
                tendencia=tendencia,
                confianza=confianza,
                umbral=_umbral_o_cero(perfil),
                perfil=perfil,
            )
        if not senal.operable:
            return Decision(
                accion="SIN_SENAL",
                motivo=senal.motivo,
                tendencia=senal.tendencia,
                confianza=senal.confianza,
                umbral=senal.umbral,
                perfil=senal.perfil,
            )

        # 3) El modo, al final: ni el modo ni la confianza importan si el
        #    sistema está bloqueado o la señal no se puede defender.
        if not autonomo:
            return Decision(
                accion="ALERTA",
                motivo=f"{MOTIVO_ASESOR} {senal.motivo}",
                tendencia=senal.tendencia,
                confianza=senal.confianza,
                umbral=senal.umbral,
                perfil=senal.perfil,
            )

        tipo = "BUY" if tendencia == "UP" else "SELL"
        return Decision(
            accion="ORDEN",
            motivo=f"{MOTIVO_AUTONOMO} {senal.motivo}",
            tendencia=senal.tendencia,
            confianza=senal.confianza,
            umbral=senal.umbral,
            perfil=senal.perfil,
            orden=NuevaOrden(
                tipo=tipo,  # type: ignore[arg-type]
                cantidad=self._cantidad,
                prediction_id=prediction_id,
            ),
        )


def _umbral_o_cero(perfil: str) -> float:
    """Umbral para la respuesta cuando el perfil ni siquiera se pudo resolver."""
    try:
        return umbral_de(perfil)
    except ValueError:
        return 0.0
