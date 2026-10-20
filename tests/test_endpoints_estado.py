"""`GET /api/health` y `GET /api/status` — la regla de estado, extremo a extremo.

La regla vive en `src/endpoints/estado.py::_regla` y aquí se prueba a través
del endpoint con dobles, que es como la usará el dashboard cada 10 s (D8).
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.database.configuraciones import ConfiguracionSistema
from src.database.predictions import PrediccionGuardada
from src.endpoints.estado import _regla
from src.endpoints.esquemas import ESTADOS_SISTEMA, IaSalida
from src.endpoints.errores import instalar_manejadores
from tests.fakes import (
    AHORA_EN_SESION,
    AHORA_FIN_DE_SEMANA,
    INTERVALO_MS,
    APERTURA_MS,
    ClienteIaFalso,
    ConfiguracionesFalso,
    LectorFalso,
    PrediccionesFalso,
    cliente_de_prueba,
    estado_de_prueba,
    serie_sesion,
)


def _velas_que_terminan(hace_minutos: float, n: int = 30):
    """Sesión cuya última vela cierra `hace_minutos` antes de la hora fijada."""
    ahora_ms = int(AHORA_EN_SESION.timestamp() * 1000)
    fin = ahora_ms - int(hace_minutos * 60_000)
    return serie_sesion(fin - (n - 1) * INTERVALO_MS, n)


class TestSalud:
    def test_health_responde_sin_estado_ni_servicios(self) -> None:
        # /api/health no toca ni InfluxDB ni PostgreSQL: es el healthcheck del
        # contenedor, y debe funcionar aunque los dos estén caídos.
        cliente = TestClient(FastAPI())
        instalar_manejadores(cliente.app)
        from src.endpoints import salud

        cliente.app.include_router(salud.router)
        respuesta = cliente.get("/api/health")
        assert respuesta.status_code == 200
        assert respuesta.json() == {"estado": "ok", "servicio": "api", "version": "0.1.0"}


class TestVerde:
    def test_dato_fresco_ia_ok_y_sin_prediccion_es_verde(self) -> None:
        cuerpo = cliente_de_prueba(
            estado_de_prueba(velas=_velas_que_terminan(hace_minutos=5))
        ).get("/api/status").json()

        assert cuerpo["estado"] == "VERDE"
        assert cuerpo["mercado"]["abierto"] is True
        assert cuerpo["ia"]["disponible"] is True
        assert cuerpo["modo"]["nombre"] == "ASESOR"
        assert cuerpo["modo"]["autonomo_disponible"] is False
        assert cuerpo["senal"]["operable"] is False  # aún no hay predicción

    def test_ultima_prediccion_stable_tambien_es_verde(self) -> None:
        repo = PrediccionesFalso()
        repo.filas.append(
            _fila_prediccion("STABLE", 0.9)
        )
        cuerpo = cliente_de_prueba(
            estado_de_prueba(velas=_velas_que_terminan(5), predicciones=repo)
        ).get("/api/status").json()

        assert cuerpo["estado"] == "VERDE"
        assert cuerpo["ultima_prediccion"]["trend_prediction"] == "STABLE"
        assert cuerpo["senal"]["operable"] is False  # STABLE nunca opera (D7)
        assert "STABLE nunca es una señal operable" in cuerpo["senal"]["motivo"]

    def test_ventana_incompleta_no_pone_amarillo(self) -> None:
        # Decisión D6: son las primeras 12 velas del día, el sistema está
        # esperando datos que aún no existen, no degradado.
        cuerpo = cliente_de_prueba(
            estado_de_prueba(velas=_velas_que_terminan(5, n=5))
        ).get("/api/status").json()

        assert cuerpo["estado"] == "VERDE"
        assert any("ventana" in aviso for aviso in cuerpo["avisos"])


class TestAmarillo:
    def test_dato_desactualizado_durante_sesion(self) -> None:
        cuerpo = cliente_de_prueba(
            estado_de_prueba(velas=_velas_que_terminan(hace_minutos=20))
        ).get("/api/status").json()

        assert cuerpo["estado"] == "AMARILLO"
        assert "Sin datos en horario de mercado" in cuerpo["motivo"]
        assert cuerpo["retraso_max_s"] == 600  # 2 intervalos de vela

    def test_ia_no_responde(self) -> None:
        cuerpo = cliente_de_prueba(
            estado_de_prueba(
                velas=_velas_que_terminan(5),
                cliente_ia=ClienteIaFalso(error=ConnectionError("M1 caída")),
            )
        ).get("/api/status").json()

        assert cuerpo["estado"] == "AMARILLO"
        assert "IA no disponible" in cuerpo["motivo"]
        assert cuerpo["ia"]["disponible"] is False

    def test_servicio_vivo_pero_modelo_sin_cargar(self) -> None:
        cuerpo = cliente_de_prueba(
            estado_de_prueba(
                velas=_velas_que_terminan(5),
                cliente_ia=ClienteIaFalso(modelo_cargado=False),
            )
        ).get("/api/status").json()

        assert cuerpo["estado"] == "AMARILLO"
        assert "modelo no está cargado" in cuerpo["ia"]["detalle"]

    def test_ultima_prediccion_up_pone_amarillo(self) -> None:
        repo = PrediccionesFalso()
        repo.filas.append(_fila_prediccion("UP", 0.9))
        cuerpo = cliente_de_prueba(
            estado_de_prueba(velas=_velas_que_terminan(5), predicciones=repo)
        ).get("/api/status").json()

        assert cuerpo["estado"] == "AMARILLO"
        assert "es UP, no STABLE" in cuerpo["motivo"]
        # Y la señal se evalúa igual: 0,9 ≥ 0,75 del perfil por defecto.
        assert cuerpo["senal"]["operable"] is True
        assert cuerpo["senal"]["umbral"] == 0.75

    def test_postgres_caído_no_se_disfraza_de_verde(self) -> None:
        cuerpo = cliente_de_prueba(
            estado_de_prueba(
                velas=_velas_que_terminan(5),
                configuraciones=ConfiguracionesFalso(
                    error=RuntimeError("connection refused")
                ),
            )
        ).get("/api/status").json()

        assert cuerpo["estado"] == "AMARILLO"
        assert "PostgreSQL" in cuerpo["motivo"]
        # La configuración mostrada es la por defecto y LO DICE.
        assert cuerpo["perfil"]["por_defecto"] is True
        assert any("PostgreSQL no responde" in aviso for aviso in cuerpo["avisos"])


class TestCerrado:
    def test_fin_de_semana_es_cerrado_y_neutro(self) -> None:
        estado = estado_de_prueba(ahora=AHORA_FIN_DE_SEMANA)
        estado.lector = LectorFalso(serie_sesion(APERTURA_MS, 78))
        cuerpo = cliente_de_prueba(estado).get("/api/status").json()

        assert cuerpo["estado"] == "CERRADO"
        assert cuerpo["motivo"] == "Mercado cerrado: Fin de semana."
        assert cuerpo["mercado"]["motivo"] == "Fin de semana"

    def test_cerrado_no_acumula_causas_de_sesion(self) -> None:
        # Con la IA caída y el dato viejo, fuera de sesión el motivo sigue
        # siendo solo el cierre: «dato desactualizado» a las 2 a.m. es ruido.
        estado = estado_de_prueba(
            ahora=AHORA_FIN_DE_SEMANA,
            cliente_ia=ClienteIaFalso(error=ConnectionError("M1 caída")),
        )
        estado.lector = LectorFalso(serie_sesion(APERTURA_MS, 78))
        cuerpo = cliente_de_prueba(estado).get("/api/status").json()

        assert cuerpo["estado"] == "CERRADO"
        assert cuerpo["motivo"] == "Mercado cerrado: Fin de semana."
        assert cuerpo["ia"]["disponible"] is False  # el dato de la IA se informa


class TestConfiguracion:
    def test_sin_fila_usa_los_por_defecto_y_lo_dice(self) -> None:
        cuerpo = cliente_de_prueba(
            estado_de_prueba(velas=_velas_que_terminan(5))
        ).get("/api/status").json()

        assert cuerpo["perfil"]["nombre"] == "MODERATE"
        assert cuerpo["perfil"]["umbral_confianza"] == 0.75
        assert cuerpo["perfil"]["por_defecto"] is True
        assert any("Sin fila de configuración" in a for a in cuerpo["avisos"])

    def test_con_fila_usa_el_perfil_guardado(self) -> None:
        config = ConfiguracionesFalso(
            ConfiguracionSistema(max_loss_day=1.25, is_autonomous=False, risk_profile="CONVENTIONAL")
        )
        cuerpo = cliente_de_prueba(
            estado_de_prueba(velas=_velas_que_terminan(5), configuraciones=config)
        ).get("/api/status").json()

        assert cuerpo["perfil"]["nombre"] == "CONVENTIONAL"
        assert cuerpo["perfil"]["umbral_confianza"] == 0.85
        assert cuerpo["perfil"]["por_defecto"] is False

    def test_perfil_desconocido_cae_a_moderate_con_aviso(self) -> None:
        config = ConfiguracionesFalso(
            ConfiguracionSistema(max_loss_day=1.25, is_autonomous=False, risk_profile="LOCO")
        )
        cuerpo = cliente_de_prueba(
            estado_de_prueba(velas=_velas_que_terminan(5), configuraciones=config)
        ).get("/api/status").json()

        assert cuerpo["perfil"]["nombre"] == "MODERATE"
        assert any("desconocido" in aviso for aviso in cuerpo["avisos"])

    def test_fila_en_autonomo_informa_que_no_se_ejecuta(self) -> None:
        config = ConfiguracionesFalso(
            ConfiguracionSistema(max_loss_day=1.25, is_autonomous=True, risk_profile="MODERATE")
        )
        cuerpo = cliente_de_prueba(
            estado_de_prueba(velas=_velas_que_terminan(5), configuraciones=config)
        ).get("/api/status").json()

        assert cuerpo["modo"]["nombre"] == "AUTONOMO"
        assert cuerpo["modo"]["autonomo_disponible"] is False  # ni siquiera en TRUE
        assert any("Modo Autónomo" in aviso for aviso in cuerpo["avisos"])


class TestReglaPura:
    """`_regla` sin endpoint, para los casos límite del umbral de antigüedad."""

    def _datos(self, **cambios):
        base = dict(
            abierto=True,
            motivo_mercado="Horario regular de mercado",
            ultima_vela_ms=1_000_000,
            ahora_ms=1_000_000 + 600_000,  # justo en el umbral de 10 min
            retraso_max_s=600,
            vela_error=None,
            ia=IaSalida(disponible=True, detalle="ok"),
            ultima_prediccion=None,
            error_pg=None,
        )
        base.update(cambios)
        return base

    def test_exactamente_en_el_umbral_sigue_verde(self) -> None:
        assert _regla(**self._datos())[0] == "VERDE"

    def test_un_segundo_mas_es_amarillo(self) -> None:
        assert _regla(**self._datos(ahora_ms=1_000_000 + 601_000))[0] == "AMARILLO"

    def test_el_estado_devuelto_pertenece_al_contrato(self) -> None:
        assert _regla(**self._datos())[0] in ESTADOS_SISTEMA
        assert _regla(**self._datos(abierto=False))[0] in ESTADOS_SISTEMA
        assert _regla(**self._datos(ia=IaSalida(disponible=False, detalle="x")))[0] in ESTADOS_SISTEMA


def _fila_prediccion(tendencia: str, confianza: float) -> PrediccionGuardada:
    ahora_ms = int(AHORA_EN_SESION.timestamp() * 1000)
    return PrediccionGuardada(
        id="fila-1",
        prediction_time=ahora_ms,
        trend_prediction=tendencia,
        confidence_score=confianza,
        technical_rationale="Razón XAI de prueba.",
    )
