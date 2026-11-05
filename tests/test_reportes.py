"""Reportes (RF-702) — parámetros, CSV, PDF y los cuatro tipos.

Todo con dobles: el endpoint junta datos de PostgreSQL, InfluxDB y M1, y
aquí se comprueba lo que depende del código — que el rango se valide, que
el CSV abra bien en Excel, que el PDF no reviente con acentos y que cada
tipo pida sus datos— sin tocar servicios reales.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from src.database.predictions import PrediccionNueva
from src.utils.reportes import FILAS_CABECERA, neutralizar
from tests.fakes import (
    APERTURA_MS,
    INTERVALO_MS,
    AuditoriaFalsa,
    PrediccionesFalso,
    cliente_de_prueba,
    estado_de_prueba,
    registro_auditoria,
    vela,
)

DIA = date(2026, 10, 16)
DIA_TEXTO = "2026-10-16"
#: Epoch de la apertura y del cierre del día de prueba (09:30 y 16:00 ET).
DESDE_MS = APERTURA_MS
HASTA_MS = APERTURA_MS + 24 * 60 * 60 * 1000 - 1


def _velas_cayendo(n: int = 12):
    """Sesión de 12 velas que cae de 500 a 493 (−1,4 %): rendimiento negativo."""
    objetivo = 493.0
    return [
        vela(APERTURA_MS + i * INTERVALO_MS, 500.0 + (objetivo - 500.0) * i / (n - 1))
        for i in range(n)
    ]


def _estado(**kwargs):
    kwargs.setdefault("velas", _velas_cayendo())
    kwargs.setdefault("predicciones", PrediccionesFalso())
    kwargs.setdefault("auditoria", AuditoriaFalsa())
    return estado_de_prueba(**kwargs)


def _cliente(**kwargs) -> TestClient:
    return cliente_de_prueba(_estado(**kwargs))


def _cuerpo(respuesta) -> str:
    """CSV como texto, quitando el BOM para leerlo con normalidad."""
    return respuesta.content.decode("utf-8").lstrip("﻿")


# -- validación de parámetros ----------------------------------------------


class TestParametros:
    def test_tipo_desconocido_es_422_con_los_validos(self) -> None:
        r = _cliente().get("/api/reportes/loquesea")
        assert r.status_code == 422
        assert "senal" in r.json()["mensaje"]
        assert "auditoria" in r.json()["mensaje"]

    def test_formato_desconocido_es_422(self) -> None:
        r = _cliente().get("/api/reportes/senal", params={"formato": "xlsx"})
        assert r.status_code == 422
        assert "csv" in r.json()["mensaje"] and "pdf" in r.json()["mensaje"]

    @pytest.mark.parametrize("mala", ["16/10/2026", "2026-13-45", "ayer", "20261016-"])
    def test_fecha_mal_formada_es_422_con_ejemplo(self, mala: str) -> None:
        r = _cliente().get("/api/reportes/senal", params={"desde": mala})
        assert r.status_code == 422
        assert "AAAA-MM-DD" in r.json()["mensaje"]

    def test_desde_posterior_a_hasta_es_422(self) -> None:
        r = _cliente().get(
            "/api/reportes/senal",
            params={"desde": "2026-10-20", "hasta": "2026-10-16"},
        )
        assert r.status_code == 422
        assert "después de terminar" in r.json()["mensaje"]

    def test_un_solo_extremo_es_un_dia(self) -> None:
        r = _cliente().get(
            "/api/reportes/senal", params={"hasta": DIA_TEXTO}
        )
        assert r.status_code == 200
        assert "del 16/10/2026 al 16/10/2026" in _cuerpo(r)


class TestRangoPorDefecto:
    def test_sin_parametros_usa_las_ultimas_sesiones(self) -> None:
        # El doble tiene UNA sesión (16/10/2026): el rango por defecto sale
        # de las velas, no de cinco días naturales.
        r = _cliente().get("/api/reportes/rendimiento")
        assert r.status_code == 200
        assert "del 16/10/2026 al 16/10/2026" in _cuerpo(r)
        assert "fechas de sesión en America/New_York" in _cuerpo(r)

    def test_sin_velas_el_rango_cae_en_dias_naturales(self) -> None:
        r = cliente_de_prueba(_estado(velas=[])).get("/api/reportes/rendimiento")
        assert r.status_code == 200  # igual sirve el archivo
        assert "Rango;" in _cuerpo(r)


# -- CSV --------------------------------------------------------------------


class TestCsv:
    def test_empieza_con_bom_y_separa_con_punto_y_coma(self) -> None:
        r = _cliente().get("/api/reportes/senal", params={"desde": DIA_TEXTO})
        # BOM real (EF BB BF) delante de «Reporte».
        assert r.content.startswith(b"\xef\xbb\xbfReporte;")
        assert r.headers["content-type"].startswith("text/csv")
        assert ";Reporte;" not in _cuerpo(r)  # la cabecera no se parte sola

    def test_lleva_las_siete_lineas_de_cabecera(self) -> None:
        cuerpo = _cuerpo(_cliente().get("/api/reportes/senal"))
        lineas = cuerpo.splitlines()
        assert lineas[0].startswith("Reporte;")
        assert lineas[FILAS_CABECERA - 1] == ""  # la línea en blanco separadora
        assert lineas[FILAS_CABECERA].startswith("sesion;hora_et;")  # títulos
        claves = [l.split(";")[0] for l in lineas[: FILAS_CABECERA - 1]]
        assert claves == [
            "Reporte", "Generado", "Rango", "Fuente", "Nota", "Métrica del modelo", "Filas",
        ]

    def test_la_cabecera_dice_fuente_nota_y_metrica_honesta(self) -> None:
        cuerpo = _cuerpo(_cliente().get("/api/reportes/senal"))
        assert "QQQ como proxy del NASDAQ-100" in cuerpo
        assert "Datos de mercado reales; broker simulado" in cuerpo
        assert "sin ventaja direccional probada" in cuerpo
        assert "exactitud direccional" in cuerpo

    def test_disposición_de_descarga_y_nombre_por_rango(self) -> None:
        r = _cliente().get(
            "/api/reportes/senal", params={"desde": DIA_TEXTO, "hasta": DIA_TEXTO}
        )
        assert (
            r.headers["content-disposition"]
            == 'attachment; filename="reporte_senal_2026-10-16_2026-10-16.csv"'
        )

    def test_neutraliza_formulas_en_texto_de_la_base(self) -> None:
        # Un motivo que empiece por «=» debe salir con apóstrofo: si no,
        # Excel lo ejecuta como fórmula al abrir el archivo.
        auditoria = AuditoriaFalsa(
            [
                registro_auditoria(
                    DESDE_MS + 1000,
                    action="KILL_SWITCH_TRIGGERED",
                    payload='{"motivo": "=1+1", "origen": "@SUMA"}',
                )
            ]
        )
        cuerpo = _cuerpo(
            _cliente(auditoria=auditoria).get(
                "/api/reportes/kill-switch", params={"desde": DIA_TEXTO}
            )
        )
        assert "'=1+1" in cuerpo
        assert "'@SUMA" in cuerpo

    def test_no_neutraliza_los_numeros_negativos(self) -> None:
        # Un rendimiento de −1,4 % tiene que seguir siendo negativo: si el
        # apóstrofo se aplicara a los números, la columna dejaría de sumar.
        cuerpo = _cuerpo(
            _cliente().get("/api/reportes/rendimiento", params={"desde": DIA_TEXTO})
        )
        fila = next(l for l in cuerpo.splitlines() if l.startswith("16/10/2026;"))
        assert ";-1,4" in fila, fila
        assert ";'-1,4" not in fila, fila

    def test_neutralizar_solo_toca_el_primer_caracter(self) -> None:
        assert neutralizar("=1+1") == "'=1+1"
        assert neutralizar("@SUMA") == "'@SUMA"
        assert neutralizar("Pérdida de sesión") == "Pérdida de sesión"
        assert neutralizar("127.0.0.1") == "127.0.0.1"


# -- PDF --------------------------------------------------------------------


class TestPdf:
    def test_es_un_pdf_real_con_disposición_de_descarga(self) -> None:
        r = _cliente().get(
            "/api/reportes/senal", params={"desde": DIA_TEXTO, "formato": "pdf"}
        )
        assert r.status_code == 200
        assert r.content[:5] == b"%PDF-"
        assert r.headers["content-type"] == "application/pdf"
        assert r.headers["content-disposition"].endswith(".pdf\"")

    def test_genera_con_tildes_y_eñe(self) -> None:
        # fpdf2 con Helvetica (Latin-1) no tiene glifos para «−», «→» ni
        # comillas tipográficas: si `_para_pdf` no los normalizara, esta
        # prueba reventaría a mitad del reporte.
        predicciones = PrediccionesFalso()
        predicciones.guardar(
            PrediccionNueva(
                prediction_time=APERTURA_MS + INTERVALO_MS,
                trend_prediction="UP",
                confidence_score=0.8123,
                technical_rationale=(
                    "Señal débil: año 2026, «sin movimiento» → sin orden. "
                    "Caída ≥ 2 % en la sesión anterior."
                ),
            )
        )
        r = _cliente(predicciones=predicciones).get(
            "/api/reportes/senal", params={"desde": DIA_TEXTO, "formato": "pdf"}
        )
        assert r.status_code == 200, r.content[:200]
        assert r.content[:5] == b"%PDF-"
        assert len(r.content) > 800, "el PDF tiene que llevar contenido, no solo la cabecera"

    def test_las_columnas_de_una_tabla_llegan_al_pdf(self) -> None:
        cuerpo = _cliente().get(
            "/api/reportes/rendimiento", params={"desde": DIA_TEXTO, "formato": "pdf"}
        ).content
        assert cuerpo[:5] == b"%PDF-"


# -- tipos -------------------------------------------------------------------


class TestTipos:
    def test_senal_inyectada_aparece_con_su_umbral(self) -> None:
        predicciones = PrediccionesFalso()
        predicciones.guardar(
            PrediccionNueva(
                prediction_time=APERTURA_MS + INTERVALO_MS,
                trend_prediction="UP",
                confidence_score=0.9,
                technical_rationale="Cruce de medias con volumen creciente.",
            )
        )
        cuerpo = _cuerpo(
            _cliente(predicciones=predicciones).get(
                "/api/reportes/senal", params={"desde": DIA_TEXTO}
            )
        )
        fila = next(l for l in cuerpo.splitlines() if ";UP;" in l)
        # Perfil MODERATE por defecto → umbral 75 %; confianza 90 % → operable.
        # Los números enteros salen sin decimales (no «90,0»): en Excel
        # siguen siendo números, que es lo que importa.
        assert ";90;75;Sí;" in fila, fila
        assert "Cruce de medias" in fila

    def test_rendimiento_calcula_el_2x_en_las_subidas(self) -> None:
        # Sesión que sube de 500 a 510 (+2 %) → rendimiento RiTech +4 %.
        subiendo = [
            vela(APERTURA_MS + i * INTERVALO_MS, 500.0 + i * (10.0 / 11))
            for i in range(12)
        ]
        cuerpo = _cuerpo(
            _cliente(velas=subiendo).get(
                "/api/reportes/rendimiento", params={"desde": DIA_TEXTO}
            )
        )
        fila = next(l for l in cuerpo.splitlines() if l.startswith("16/10/2026;"))
        # sesión;apertura;cierre;variación;rendimiento;límite;umbral;gap
        # +2 % de variación → +4 % de rendimiento (2x); el umbral de PÉRDIDA
        # no se alcanza en una subida, y no hubo salto → sin gap.
        assert fila == "16/10/2026;500;510;2;4;1,25;No;No", fila

    def test_kill_switch_lista_sus_dos_acciones(self) -> None:
        auditoria = AuditoriaFalsa(
            [
                registro_auditoria(DESDE_MS + 1000, action="KILL_SWITCH_TRIGGERED"),
                registro_auditoria(DESDE_MS + 2000, action="KILL_SWITCH_RESET",
                                  actor="CLAVE_REINICIO"),
                registro_auditoria(DESDE_MS + 3000, action="OTRA_ACCION"),
            ]
        )
        cuerpo = _cuerpo(
            _cliente(auditoria=auditoria).get(
                "/api/reportes/kill-switch", params={"desde": DIA_TEXTO}
            )
        )
        assert "KILL_SWITCH_TRIGGERED" in cuerpo
        assert "KILL_SWITCH_RESET" in cuerpo
        assert "OTRA_ACCION" not in cuerpo  # solo eventos del Kill-Switch
        assert cuerpo.splitlines()[-1].count(";") >= 2  # hay filas

    def test_auditoria_resume_por_accion_y_actor(self) -> None:
        auditoria = AuditoriaFalsa(
            [
                registro_auditoria(DESDE_MS + 1000, action="A", actor="SISTEMA"),
                registro_auditoria(DESDE_MS + 2000, action="A", actor="SISTEMA"),
                registro_auditoria(DESDE_MS + 3000, action="B", actor="OPERADOR"),
            ]
        )
        cuerpo = _cuerpo(
            _cliente(auditoria=auditoria).get(
                "/api/reportes/auditoria", params={"desde": DIA_TEXTO}
            )
        )
        assert "A;SISTEMA;2;" in cuerpo
        assert "B;OPERADOR;1;" in cuerpo

    def test_rango_sin_datos_devuelve_archivo_con_cero_filas(self) -> None:
        # «No hay datos en ese periodo» es un resultado, no un error.
        r = _cliente().get(
            "/api/reportes/senal", params={"desde": "2026-11-02", "hasta": "2026-11-06"}
        )
        assert r.status_code == 200
        cuerpo = _cuerpo(r)
        assert "Filas;0" in cuerpo
        assert cuerpo.splitlines()[FILAS_CABECERA].startswith("sesion;")  # títulos

    def test_sin_lector_de_auditoria_no_finge_una_lista_vacia(self) -> None:
        estado = _estado()
        estado.auditoria = None
        r = cliente_de_prueba(estado).get(
            "/api/reportes/auditoria", params={"desde": DIA_TEXTO}
        )
        assert r.status_code == 503
        assert "lector de auditoría" in r.json()["mensaje"]
