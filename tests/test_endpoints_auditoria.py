"""`GET /api/auditoria` — visor de solo lectura (RF-701).

La prueba más importante de este archivo es `test_no_hay_escrituras`: la
auditoría se consulta desde la interfaz y se escribe desde el backend, y
que aparezca un `POST` en esta ruta sería una puerta a un registro
inmutable.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from src.endpoints.app import crear_app
from tests.fakes import (
    APERTURA_MS,
    AuditoriaFalsa,
    cliente_de_prueba,
    estado_de_prueba,
    registro_auditoria,
    serie_sesion,
)

DIA = date(2026, 10, 16)
DIA_TEXTO = "2026-10-16"


def _estado(**kwargs):
    kwargs.setdefault("velas", serie_sesion(APERTURA_MS, 12))
    kwargs.setdefault(
        "auditoria",
        AuditoriaFalsa(
            [
                registro_auditoria(APERTURA_MS + 1000, action="KILL_SWITCH_TRIGGERED",
                                   actor="OPERADOR"),
                registro_auditoria(APERTURA_MS + 2000, action="KILL_SWITCH_RESET",
                                   actor="CLAVE_REINICIO"),
                registro_auditoria(APERTURA_MS + 3000, action="MODO_CAMBIADO",
                                   actor="SISTEMA"),
            ]
        ),
    )
    return estado_de_prueba(**kwargs)


def _cliente(**kwargs) -> TestClient:
    return cliente_de_prueba(_estado(**kwargs))


class TestRuta:
    def test_devuelve_registros_con_sus_campos(self) -> None:
        r = _cliente().get("/api/auditoria", params={"desde": DIA_TEXTO})
        assert r.status_code == 200
        cuerpo = r.json()
        assert cuerpo["total"] == 3
        assert len(cuerpo["registros"]) == 3
        primero = cuerpo["registros"][0]
        assert primero["action"] == "MODO_CAMBIADO"  # más reciente primero
        assert primero["actor"] == "SISTEMA"
        assert primero["ip_address"] == "127.0.0.1"
        assert "/" in primero["fecha"] and ":" in primero["fecha"]  # DD/MM/YYYY HH:mm:ss

    def test_solo_hay_un_get_y_ninguna_escritura(self) -> None:
        # RF-701 + la regla de FASE D: cero rutas de escritura. Se mira el
        # contrato OpenAPI (no `app.routes`, que en FastAPI actual agrupa los
        # routers en `_IncludedRouter` sin exponer el path).
        cliente = cliente_de_prueba(_estado())
        contrato = cliente.app.openapi()["paths"]["/api/auditoria"]
        assert set(contrato) == {"get"}, f"métodos expuestos: {sorted(contrato)}"

    @pytest.mark.parametrize("metodo", ["post", "put", "delete", "patch"])
    def test_los_metodos_de_escritura_no_existen(self, metodo: str) -> None:
        r = getattr(_cliente(), metodo)("/api/auditoria")
        assert r.status_code == 405
        assert r.json()["mensaje"] == "Método no permitido."

    def test_lleva_la_nota_de_inmutabilidad(self) -> None:
        cuerpo = _cliente().get("/api/auditoria", params={"desde": DIA_TEXTO}).json()
        assert cuerpo["nota"] == "Registros inmutables a nivel de base de datos (RF-701)."


class TestFiltros:
    def test_filtra_por_actor(self) -> None:
        cuerpo = _cliente().get(
            "/api/auditoria", params={"desde": DIA_TEXTO, "actor": "SISTEMA"}
        ).json()
        assert cuerpo["total"] == 1
        assert cuerpo["registros"][0]["actor"] == "SISTEMA"

    def test_filtra_por_accion(self) -> None:
        cuerpo = _cliente().get(
            "/api/auditoria", params={"desde": DIA_TEXTO, "accion": "KILL_SWITCH_RESET"}
        ).json()
        assert cuerpo["total"] == 1
        assert cuerpo["registros"][0]["action"] == "KILL_SWITCH_RESET"

    def test_un_actor_que_no_existe_da_lista_vacia_y_no_error(self) -> None:
        cuerpo = _cliente().get(
            "/api/auditoria", params={"desde": DIA_TEXTO, "actor": "NADIE"}
        ).json()
        assert cuerpo["total"] == 0
        assert cuerpo["registros"] == []

    def test_pagina_respetando_el_limite_de_la_api(self) -> None:
        cuerpo = _cliente().get(
            "/api/auditoria", params={"desde": DIA_TEXTO, "limit": 2, "offset": 0}
        ).json()
        assert len(cuerpo["registros"]) == 2
        assert cuerpo["total"] == 3
        assert cuerpo["limit"] == 2
        assert cuerpo["offset"] == 0

    def test_offset_fuera_de_rango_devuelve_vacio_pero_con_el_total(self) -> None:
        cuerpo = _cliente().get(
            "/api/auditoria", params={"desde": DIA_TEXTO, "limit": 50, "offset": 500}
        ).json()
        assert cuerpo["registros"] == []
        assert cuerpo["total"] == 3


class TestRango:
    def test_sin_fechas_usa_las_ultimas_sesiones(self) -> None:
        cuerpo = _cliente().get("/api/auditoria").json()
        assert cuerpo["desde"] == "2026-10-16"
        assert cuerpo["hasta"] == "2026-10-16"

    def test_rango_explicito(self) -> None:
        cuerpo = _cliente().get(
            "/api/auditoria", params={"desde": "2026-11-01", "hasta": "2026-11-07"}
        ).json()
        assert cuerpo["desde"] == "2026-11-01"
        assert cuerpo["hasta"] == "2026-11-07"
        assert cuerpo["total"] == 0  # ese rango no tiene eventos en el doble

    @pytest.mark.parametrize("mala", ["16/10/2026", "no-fecha"])
    def test_fecha_invalida_es_422(self, mala: str) -> None:
        r = _cliente().get("/api/auditoria", params={"desde": mala})
        assert r.status_code == 422
        assert "AAAA-MM-DD" in r.json()["mensaje"]

    def test_rango_invertido_es_422(self) -> None:
        r = _cliente().get(
            "/api/auditoria", params={"desde": "2026-11-07", "hasta": "2026-11-01"}
        )
        assert r.status_code == 422
        assert "después de terminar" in r.json()["mensaje"]

    @pytest.mark.parametrize("limit", [0, 201, -1])
    def test_limit_fuera_de_la_ventana_es_422(self, limit: int) -> None:
        r = _cliente().get("/api/auditoria", params={"limit": limit})
        assert r.status_code == 422


class TestFallos:
    def test_sin_lector_responde_503_y_no_una_lista_vacia(self) -> None:
        estado = _estado()
        estado.auditoria = None
        r = cliente_de_prueba(estado).get("/api/auditoria")
        assert r.status_code == 503
        assert "lector de auditoría" in r.json()["mensaje"]

    def test_base_caida_responde_503_en_espanol(self) -> None:
        from tests.fakes import AuditoriaFalsa

        class _Roto(AuditoriaFalsa):
            def listar(self, filtro):
                raise RuntimeError("postgres no responde")

        estado = _estado(auditoria=_Roto())
        r = cliente_de_prueba(estado).get("/api/auditoria", params={"desde": DIA_TEXTO})
        assert r.status_code == 503
        assert "postgres no responde" in r.json()["mensaje"]

    def test_el_estado_de_los_que_estan_al_lado_no_se_tocó(self) -> None:
        # Montar la app entera con el visor instalado no cambia el resto de
        # rutas: el contrato del panel sigue intacto.
        cliente = cliente_de_prueba(_estado())
        assert cliente.get("/api/health").status_code == 200
        assert crear_app(_estado()) is not None
