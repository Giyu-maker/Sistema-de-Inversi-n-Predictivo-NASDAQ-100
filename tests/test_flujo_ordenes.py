"""Flujo de órdenes con predicciones INYECTADAS — RF-401/402/403, RF-601/602.

⚠️ **Esto verifica el PIPELINE, no el rendimiento del modelo.**

El backlog del Ítem 9 lo manda así explícitamente: en el entrenamiento del
Sprint 3 el 100 % de las predicciones por encima de 0,65 fueron `STABLE` y
ninguna `UP`/`DOWN` alcanzó siquiera el umbral más bajo, así que con el
modelo real el sistema no abriría posición alguna. Esa abstención es el
comportamiento correcto (RF-304) y no un defecto: por eso aquí se INYECTAN
las predicciones, para poder recorrer los cuatro caminos del flujo —alerta,
orden simulada, abstención y bloqueo— sin depender de qué decida el modelo.

Lo que se encadena de verdad, con las clases de producción:
`PrediccionesFalso` (predicción inyectada) → `RiskManager.decidir()` →
`SimulatedBrokerGateway.enviar_orden()` → repositorio de `transactions`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from src.database.configuraciones import ConfiguracionSistema
from src.database.predictions import PrediccionNueva
from src.integration.broker import SimulatedBrokerGateway
from src.security.kill_switch import BanderaBloqueo, KillSwitchEngine
from src.security.risk_manager import RiskManager
from tests.fakes import (
    AuditorKillSwitchFalso,
    BrokerKillSwitchFalso,
    ConfiguracionesFalso,
    PrediccionesFalso,
    RepoKillSwitchFalso,
    TransaccionesFalsas,
    estado_kill_switch,
)

#: Tiempo fijo de las predicciones inyectadas (epoch ms).
TIEMPO = 1_800_000_000_000


@dataclass
class Flujo:
    """Todas las piezas del flujo, cableadas entre sí."""

    predicciones: PrediccionesFalso
    configuraciones: ConfiguracionesFalso
    transacciones: TransaccionesFalsas
    riesgo: RiskManager
    broker: SimulatedBrokerGateway
    repo_kill_switch: RepoKillSwitchFalso
    decisiones: list = field(default_factory=list)

    @property
    def ordenes(self) -> list:
        return list(self.transacciones.guardadas)


def montar(
    *,
    tendencia: str = "UP",
    confianza: float = 0.90,
    perfil: str = "MODERATE",
    autonomo: bool = False,
    kill_switch_activo: bool = False,
    inyectar: int = 1,
) -> Flujo:
    """Predicciones inyectadas + modo + perfil + motor, cableados."""
    predicciones = PrediccionesFalso()
    for i in range(inyectar):
        predicciones.guardar(
            PrediccionNueva(
                prediction_time=TIEMPO + i,
                trend_prediction=tendencia,
                confidence_score=confianza,
                technical_rationale=f"Predicción inyectada n.º {i + 1} (prueba).",
            )
        )

    configuraciones = ConfiguracionesFalso(
        ConfiguracionSistema(
            max_loss_day=1.25, is_autonomous=autonomo, risk_profile=perfil
        )
    )
    transacciones = TransaccionesFalsas()

    repo_ks = RepoKillSwitchFalso()
    if kill_switch_activo:
        repo_ks.fila = estado_kill_switch(
            activo=True, motivo="prueba", origen="MANUAL", actor="OPERADOR",
            disparado_en=TIEMPO, liquidacion_estado="COMPLETADA",
            liquidacion_en=TIEMPO, liquidaciones=1,
        )
    banda = BanderaBloqueo()
    if kill_switch_activo:
        banda.activar()
    motor = KillSwitchEngine(
        repo_ks, BrokerKillSwitchFalso(), AuditorKillSwitchFalso(),
        banda=banda, ahora_ms=lambda: TIEMPO,
    )
    return Flujo(
        predicciones=predicciones,
        configuraciones=configuraciones,
        transacciones=transacciones,
        riesgo=RiskManager(motor),
        broker=SimulatedBrokerGateway(
            transacciones,
            precio_actual=lambda _s: 512.5,
            ahora_ms=lambda: TIEMPO,
            nuevo_id=lambda: f"SIM-{len(transacciones.guardadas) + 1}",
        ),
        repo_kill_switch=repo_ks,
    )


def decidir_y_ejecutar(flujo: Flujo):
    """Lee la última predicción inyectada, decide y —si toca— emite la orden.

    Es el mismo encadenamiento que haría el ciclo en Modo Autónomo.
    """
    prediccion = flujo.predicciones.ultima()
    assert prediccion is not None, "este test necesita una predicción inyectada"
    configuracion = flujo.configuraciones.leer()
    assert configuracion is not None

    decision = flujo.riesgo.decidir(
        tendencia=prediccion.trend_prediction,
        confianza=prediccion.confidence_score,
        perfil=configuracion.risk_profile,
        autonomo=configuracion.is_autonomous,
        prediction_id=prediccion.id,
    )
    flujo.decisiones.append(decision)
    if decision.orden is not None:
        flujo.broker.enviar_orden(decision.orden)
    return decision


class TestModoAsesor:
    def test_alerta_sin_orden(self) -> None:
        flujo = montar(tendencia="UP", confianza=0.90, autonomo=False)

        decision = decidir_y_ejecutar(flujo)

        assert decision.accion == "ALERTA", "RF-401: el Asesor recomienda"
        assert decision.orden is None
        assert flujo.ordenes == [], "no se emitió ninguna orden"
        assert "Modo Asesor" in decision.motivo
        assert "recomienda" in decision.motivo

    def test_la_alerta_es_en_lenguaje_llano(self) -> None:
        flujo = montar(tendencia="DOWN", confianza=0.80, autonomo=False)
        decision = decidir_y_ejecutar(flujo)
        assert "a la baja" in decision.motivo.lower() or "baja" in decision.motivo.lower()
        assert "%" in decision.motivo


class TestModoAutonomo:
    def test_envia_orden_simulada_ligada_a_la_prediccion(self) -> None:
        flujo = montar(tendencia="UP", confianza=0.90, autonomo=True)

        decision = decidir_y_ejecutar(flujo)

        assert decision.accion == "ORDEN", "RF-402: el Autónomo ejecuta"
        assert len(flujo.ordenes) == 1
        orden = flujo.ordenes[0]
        assert orden.is_simulated is True, "RF-602: siempre simulada en esta fase"
        assert orden.transaction_type == "BUY"
        assert orden.execution_price == 512.5
        # RF-602 / SRS §4.3: la trazabilidad hasta la decisión de la IA.
        assert orden.prediction_id == flujo.predicciones.ultima().id

    @pytest.mark.parametrize(
        "tendencia,tipo", [("UP", "BUY"), ("DOWN", "SELL")]
    )
    def test_direccion_y_tipo_de_orden(self, tendencia: str, tipo: str) -> None:
        flujo = montar(tendencia=tendencia, confianza=0.90, autonomo=True)
        decidir_y_ejecutar(flujo)
        assert flujo.ordenes[0].transaction_type == tipo

    def test_stable_nunca_abre_con_ninguna_confianza(self) -> None:
        # Restricción de negocio del Ítem 9: el modelo del Sprint 3 solo
        # produjo STABLE por encima del umbral, y eso NO es un defecto.
        flujo = montar(tendencia="STABLE", confianza=0.99, autonomo=True)

        decision = decidir_y_ejecutar(flujo)

        assert decision.accion == "SIN_SENAL"
        assert decision.orden is None
        assert flujo.ordenes == []
        assert "sin movimiento" in decision.motivo.lower()

    def test_confianza_bajo_el_umbral_del_perfil_no_ordena(self) -> None:
        flujo = montar(tendencia="UP", confianza=0.70, perfil="CONVENTIONAL",
                       autonomo=True)
        decision = decidir_y_ejecutar(flujo)
        assert decision.accion == "SIN_SENAL"
        assert flujo.ordenes == []

    @pytest.mark.parametrize(
        "perfil,confianza,esperado",
        [
            ("CONVENTIONAL", 0.86, "ORDEN"),
            ("CONVENTIONAL", 0.84, "SIN_SENAL"),
            ("MODERATE", 0.76, "ORDEN"),
            ("MODERATE", 0.74, "SIN_SENAL"),
            ("AGGRESSIVE", 0.66, "ORDEN"),
            ("AGGRESSIVE", 0.64, "SIN_SENAL"),
        ],
    )
    def test_los_tres_perfiles_cambian_el_umbral(
        self, perfil: str, confianza: float, esperado: str
    ) -> None:
        # RF-403: el perfil altera el umbral de apertura.
        flujo = montar(tendencia="UP", confianza=confianza, perfil=perfil,
                       autonomo=True)
        decision = decidir_y_ejecutar(flujo)
        assert decision.accion == esperado
        assert len(flujo.ordenes) == (1 if esperado == "ORDEN" else 0)


class TestKillSwitchManda:
    def test_bloquea_la_orden_aunque_la_senal_sea_perfecta(self) -> None:
        flujo = montar(tendencia="UP", confianza=0.99, perfil="AGGRESSIVE",
                       autonomo=True, kill_switch_activo=True)

        decision = decidir_y_ejecutar(flujo)

        assert decision.accion == "BLOQUEADO"
        assert decision.orden is None
        assert flujo.ordenes == [], "RF-503: ninguna orden con el bloqueo activo"
        assert "Kill-Switch ACTIVO" in decision.motivo

    def test_bloquea_tambien_en_modo_asesor(self) -> None:
        flujo = montar(tendencia="UP", confianza=0.99, autonomo=False,
                       kill_switch_activo=True)
        decision = decidir_y_ejecutar(flujo)
        assert decision.accion == "BLOQUEADO"
        assert flujo.ordenes == []

    def test_estado_no_verificado_tampoco_ordena(self) -> None:
        flujo = montar(tendencia="UP", confianza=0.99, autonomo=True)
        flujo.repo_kill_switch.roto = True  # PostgreSQL caído

        decision = decidir_y_ejecutar(flujo)

        assert decision.accion == "BLOQUEADO"
        assert flujo.ordenes == []


class TestTrazabilidad:
    def test_cada_orden_lleva_su_prediccion_y_todo_es_simulado(self) -> None:
        flujo = montar(tendencia="UP", confianza=0.90, autonomo=True, inyectar=3)

        decidir_y_ejecutar(flujo)

        assert len(flujo.ordenes) == 1, "una predicción, una decisión, una orden"
        orden = flujo.ordenes[0]
        assert orden.is_simulated is True
        assert orden.prediction_id == flujo.predicciones.ultima().id
        assert orden.broker_order_id.startswith("SIM-")

    def test_la_ultima_predicción_inyectada_es_la_que_decide(self) -> None:
        flujo = montar(tendencia="STABLE", confianza=0.90, autonomo=True, inyectar=1)
        # Se inyecta una segunda, más reciente, operable.
        flujo.predicciones.guardar(
            PrediccionNueva(
                prediction_time=TIEMPO + 99,
                trend_prediction="DOWN",
                confidence_score=0.88,
                technical_rationale="Segunda inyectada.",
            )
        )

        decision = decidir_y_ejecutar(flujo)

        assert decision.accion == "ORDEN"
        assert decision.orden is not None and decision.orden.tipo == "SELL"
        assert flujo.ordenes[0].prediction_id == flujo.predicciones.ultima().id
