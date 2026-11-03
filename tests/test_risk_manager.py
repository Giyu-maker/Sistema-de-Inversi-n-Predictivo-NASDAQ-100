"""`RiskManager` — la puerta de órdenes: bloqueo primero, luego señal, luego modo.

Con el motor del Kill-Switch sobre dobles en memoria: se prueba la POLÍTICA
(como en `test_kill_switch.py`), no el SQL, que ya está contra el motor real.
"""

from __future__ import annotations

import pytest

from src.security.kill_switch import BLOQUEO, BanderaBloqueo, KillSwitchEngine
from src.security.risk_manager import (
    CANTIDAD_POR_DEFECTO,
    RiskManager,
)
from tests.fakes import (
    AuditorKillSwitchFalso,
    BrokerKillSwitchFalso,
    RepoKillSwitchFalso,
    estado_kill_switch,
)


def manager(
    *, activo: bool = False, banda: BanderaBloqueo | None = None
) -> tuple[RiskManager, RepoKillSwitchFalso, BanderaBloqueo]:
    """RiskManager sobre un motor con el estado que se le pida."""
    repo = RepoKillSwitchFalso()
    repo.fila = estado_kill_switch(activo=activo, motivo="prueba" if activo else "Sin disparo")
    banda = banda if banda is not None else BanderaBloqueo()
    motor = KillSwitchEngine(
        repo, BrokerKillSwitchFalso(), AuditorKillSwitchFalso(),
        banda=banda, ahora_ms=lambda: 1_800_000_000_000,
    )
    return RiskManager(motor), repo, banda


class TestUmbrales:
    """SDD §5.4 `evaluate_order_risk` — los tres perfiles del AGENTS.md §8."""

    @pytest.mark.parametrize(
        "perfil,umbral",
        [("CONVENTIONAL", 0.85), ("MODERATE", 0.75), ("AGGRESSIVE", 0.65)],
    )
    def test_en_el_umbral_pasa(self, perfil: str, umbral: float) -> None:
        assert RiskManager.evaluate_order_risk(umbral, perfil) is True

    @pytest.mark.parametrize(
        "perfil,umbral",
        [("CONVENTIONAL", 0.85), ("MODERATE", 0.75), ("AGGRESSIVE", 0.65)],
    )
    def test_un_punto_menos_no_pasa(self, perfil: str, umbral: float) -> None:
        assert RiskManager.evaluate_order_risk(umbral - 0.0001, perfil) is False

    def test_un_perfil_desconocido_grita_en_lugar_de_caer_en_el_default(self) -> None:
        # El `.get(perfil, 0.75)` del SDD escondía el error; aquí no.
        with pytest.raises(ValueError, match="perfil"):
            RiskManager.evaluate_order_risk(0.9, "APACHERO")


class TestDecision:
    def test_con_kill_switch_activo_no_hay_orden_ni_alerta(self) -> None:
        manager_, _, _ = manager(activo=True)

        decision = manager_.decidir(
            tendencia="UP", confianza=0.99, perfil="AGGRESSIVE",
            autonomo=True, prediction_id=None,
        )

        assert decision.accion == "BLOQUEADO"
        assert decision.orden is None, "el bloqueo va antes que todo"
        assert "Kill-Switch ACTIVO" in decision.motivo

    def test_el_bloqueo_se_evalua_antes_que_la_senal(self) -> None:
        # Si la señal se evaluara primero, una tendencia inválida daría
        # SIN_SENAL; con el bloqueo delante, da BLOQUEADO.
        manager_, _, _ = manager(activo=True)
        decision = manager_.decidir(
            tendencia="INVENTADA", confianza=-5.0, perfil="NO_EXISTE",
            autonomo=True,
        )
        assert decision.accion == "BLOQUEADO"

    def test_estado_no_verificado_tambien_bloquea(self) -> None:
        # «No lo sé» no es «no bloqueado»: no se emite nada.
        repo = RepoKillSwitchFalso()
        repo.roto = True  # PostgreSQL caído
        motor = KillSwitchEngine(
            repo, BrokerKillSwitchFalso(), AuditorKillSwitchFalso(),
            banda=BanderaBloqueo(), ahora_ms=lambda: 1,
        )
        decision = RiskManager(motor).decidir(
            tendencia="UP", confianza=0.99, perfil="AGGRESSIVE", autonomo=True
        )
        assert decision.accion == "BLOQUEADO"
        assert "No se pudo verificar" in decision.motivo

    @pytest.mark.parametrize("tendencia", ["STABLE"])
    def test_stable_nunca_abre_ni_en_autonomo(self, tendencia: str) -> None:
        # Restricción de negocio del Ítem 9: STABLE no abre con ninguna
        # confianza. (En el entrenamiento del Sprint 3, el 100 % de las
        # predicciones por encima de 0,65 fueron STABLE.)
        for autonomo in (False, True):
            manager_, _, _ = manager()
            decision = manager_.decidir(
                tendencia=tendencia, confianza=0.99, perfil="AGGRESSIVE",
                autonomo=autonomo,
            )
            assert decision.accion == "SIN_SENAL"
            assert decision.orden is None
            assert "sin movimiento" in decision.motivo.lower()

    def test_confianza_bajo_el_umbral_no_es_senal(self) -> None:
        manager_, _, _ = manager()
        decision = manager_.decidir(
            tendencia="UP", confianza=0.70, perfil="CONVENTIONAL", autonomo=True
        )
        assert decision.accion == "SIN_SENAL"
        assert decision.orden is None
        assert "umbral" in decision.motivo

    def test_asesor_alerta_y_no_ordena(self) -> None:
        manager_, repo, _ = manager()
        decision = manager_.decidir(
            tendencia="UP", confianza=0.90, perfil="MODERATE", autonomo=False
        )
        assert decision.accion == "ALERTA"
        assert decision.orden is None, "RF-401: el Asesor no opera"
        assert "Modo Asesor" in decision.motivo
        assert "recomienda" in decision.motivo

    @pytest.mark.parametrize(
        "tendencia,tipo", [("UP", "BUY"), ("DOWN", "SELL")]
    )
    def test_autonomo_ordena_simulada_ligada_a_la_prediccion(
        self, tendencia: str, tipo: str
    ) -> None:
        manager_, _, _ = manager()
        decision = manager_.decidir(
            tendencia=tendencia, confianza=0.90, perfil="MODERATE",
            autonomo=True, prediction_id="3f1e0a4c-77f0-4d6e-9a1b-2c3d4e5f6071",
        )
        assert decision.accion == "ORDEN"
        assert decision.es_orden
        assert decision.orden is not None
        assert decision.orden.tipo == tipo
        assert decision.orden.cantidad == CANTIDAD_POR_DEFECTO
        assert decision.orden.prediction_id == "3f1e0a4c-77f0-4d6e-9a1b-2c3d4e5f6071"
        assert "Modo Autónomo" in decision.motivo

    def test_una_tendencia_invalida_no_produce_orden(self) -> None:
        manager_, _, _ = manager()
        decision = manager_.decidir(
            tendencia="LATERAL", confianza=0.99, perfil="AGGRESSIVE", autonomo=True
        )
        assert decision.accion == "SIN_SENAL"
        assert decision.orden is None
        assert "No se pudo evaluar la señal" in decision.motivo

    def test_una_confianza_fuera_de_rango_no_produce_orden(self) -> None:
        manager_, _, _ = manager()
        decision = manager_.decidir(
            tendencia="UP", confianza=1.7, perfil="AGGRESSIVE", autonomo=True
        )
        assert decision.accion == "SIN_SENAL"
        assert decision.orden is None

    def test_un_perfil_desconocido_no_produce_orden(self) -> None:
        manager_, _, _ = manager()
        decision = manager_.decidir(
            tendencia="UP", confianza=0.99, perfil="INVENTADO", autonomo=True
        )
        assert decision.accion == "SIN_SENAL"
        assert decision.orden is None


class TestDisparoDelegado:
    def test_trigger_emergency_kill_usa_el_mismo_motor(self) -> None:
        manager_, repo, banda = manager()
        resultado = manager_.trigger_emergency_kill(
            actor="OPERADOR", ip_address="10.0.0.7", motivo="botón"
        )
        assert resultado.primera_vez is True
        assert repo.fila.activo is True
        assert banda.activo is True

    def test_tras_el_disparo_la_siguiente_decision_se_bloquea(self) -> None:
        # El mismo motor para las dos cosas: no hay forma de disparar y
        # seguir operando en el mismo proceso.
        manager_, _, banda = manager()
        manager_.trigger_emergency_kill(
            actor="OPERADOR", ip_address="10.0.0.7", motivo="botón"
        )
        decision = manager_.decidir(
            tendencia="UP", confianza=0.99, perfil="AGGRESSIVE", autonomo=True
        )
        assert decision.accion == "BLOQUEADO"
        assert decision.orden is None

    def test_el_singleton_del_modulo_no_se_contamina_en_los_tests(self) -> None:
        # El manager de estos tests usa su propia banda, nunca la global.
        manager_, _, banda = manager()
        assert banda is not BLOQUEO
        assert BLOQUEO.activo is False
