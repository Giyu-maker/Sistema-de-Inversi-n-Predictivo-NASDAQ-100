"""Endpoints de datos — velas, predicciones, rendimiento y calidad del modelo.

Se prueban a través de la app real con dobles (mismo trazado que usará el
dashboard), incluidos los tres caminos de fallo que la API no puede esconder:

* InfluxDB caído → el historial se sirve igual y el resultado real queda null;
* PostgreSQL caído → 503 con mensaje en español, nunca un stack;
* M1 caído → `model-quality` responde 200 con `disponible=false`.
"""

from __future__ import annotations

import re

import pytest

from src.utils.fechas import fecha_hora
from tests.fakes import (
    APERTURA_MS,
    INTERVALO_MS,
    CUERPO_MODELO_EJEMPLO,
    ClienteIaFalso,
    ConfiguracionesFalso,
    LectorFalso,
    PrediccionesFalso,
    cliente_de_prueba,
    estado_de_prueba,
    serie_sesion,
    vela,
)
from src.database.predictions import PrediccionGuardada

#: Sesión completa de 78 velas (09:30 → 15:55) con cierres crecientes 500…577.
SERIE_COMPLETA = serie_sesion(APERTURA_MS, 78)

FORMATO_FECHA = re.compile(r"^\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}$")


def _fila(idx: int, tendencia: str = "UP", confianza: float = 0.8) -> PrediccionGuardada:
    """Fila cuyo `prediction_time` es el cierre de la vela `idx` de la sesión."""
    return PrediccionGuardada(
        id=f"fila-{idx}",
        prediction_time=SERIE_COMPLETA[idx].timestamp,
        trend_prediction=tendencia,
        confidence_score=confianza,
        technical_rationale=f"Razón XAI para la vela {idx}.",
    )


class TestCandles:
    def test_por_defecto_pide_las_ultimas_200(self) -> None:
        lector = LectorFalso(SERIE_COMPLETA)
        cuerpo = cliente_de_prueba(estado_de_prueba(lector=lector)).get("/api/candles").json()

        assert lector.consultas_ultimas == [200]
        assert cuerpo["total"] == 78
        assert cuerpo["aggregate"] == "5m"
        # P1.6: QQQ no es el índice — se nombra como lo que es.
        assert "QQQ" in cuerpo["nota"] and "NASDAQ-100" in cuerpo["nota"]

    def test_limit_se_pasa_al_lector(self) -> None:
        lector = LectorFalso(SERIE_COMPLETA)
        cuerpo = cliente_de_prueba(estado_de_prueba(lector=lector)).get(
            "/api/candles?limit=10"
        ).json()

        assert lector.consultas_ultimas == [10]
        assert cuerpo["total"] == 10
        # Orden ascendente: la primera fila es la más vieja del recorte.
        assert cuerpo["velas"][0]["timestamp"] == SERIE_COMPLETA[68].timestamp

    def test_rango_start_stop_se_consulta_por_rango(self) -> None:
        lector = LectorFalso(SERIE_COMPLETA)
        inicio, parada = SERIE_COMPLETA[20].timestamp, SERIE_COMPLETA[30].timestamp
        cuerpo = cliente_de_prueba(estado_de_prueba(lector=lector)).get(
            f"/api/candles?start={inicio}&stop={parada}"
        ).json()

        assert lector.consultas_rango == [(inicio, parada, "5m", None)]
        assert cuerpo["total"] == 10
        assert cuerpo["velas"][0]["timestamp"] == inicio

    def test_aggregate_se_pasa_a_influxdb(self) -> None:
        lector = LectorFalso(SERIE_COMPLETA)
        inicio, parada = SERIE_COMPLETA[0].timestamp, SERIE_COMPLETA[40].timestamp
        cliente_de_prueba(estado_de_prueba(lector=lector)).get(
            f"/api/candles?start={inicio}&stop={parada}&aggregate=15m&limit=5"
        )

        assert lector.consultas_rango == [(inicio, parada, "15m", 5)]

    def test_solo_start_es_422_y_lo_dice_en_espanol(self) -> None:
        respuesta = cliente_de_prueba(estado_de_prueba()).get(
            f"/api/candles?start={SERIE_COMPLETA[0].timestamp}"
        )

        assert respuesta.status_code == 422
        assert "deben ir juntos" in respuesta.json()["mensaje"]

    def test_aggregate_invalido_es_422_en_espanol(self) -> None:
        respuesta = cliente_de_prueba(estado_de_prueba()).get("/api/candles?aggregate=2m")

        assert respuesta.status_code == 422
        assert respuesta.json()["mensaje"] == "Petición no válida."
        assert respuesta.json()["detail"][0]["mensaje"] == "no es un valor permitido"

    def test_limit_en_cero_es_422_en_espanol(self) -> None:
        respuesta = cliente_de_prueba(estado_de_prueba()).get("/api/candles?limit=0")

        assert respuesta.status_code == 422
        assert respuesta.json()["detail"][0]["mensaje"] == "debe ser mayor o igual a 1"

    def test_influx_caído_es_503_en_espanol(self) -> None:
        estado = estado_de_prueba(lector=LectorFalso(error=ConnectionError("refused")))
        respuesta = cliente_de_prueba(estado).get("/api/candles")

        assert respuesta.status_code == 503
        assert respuesta.json()["mensaje"].startswith("La base de precios no responde")

    def test_las_fechas_van_en_formato_del_srs(self) -> None:
        cuerpo = cliente_de_prueba(
            estado_de_prueba(lector=LectorFalso(SERIE_COMPLETA))
        ).get("/api/candles?limit=1").json()

        assert FORMATO_FECHA.match(cuerpo["velas"][0]["fecha"])
        assert cuerpo["velas"][0]["fecha"] == fecha_hora(SERIE_COMPLETA[77].timestamp)


class TestPredictions:
    def test_historial_vacio(self) -> None:
        cuerpo = cliente_de_prueba(estado_de_prueba()).get("/api/predictions").json()

        assert cuerpo["predicciones"] == []
        assert cuerpo["total"] == 0
        assert cuerpo["avisos"] == []

    def test_pagina_orden_desc_y_una_sola_consulta_a_influx(self) -> None:
        repo = PrediccionesFalso()
        repo.filas.extend([_fila(15), _fila(70), _fila(40)])
        lector = LectorFalso(SERIE_COMPLETA)
        cuerpo = cliente_de_prueba(
            estado_de_prueba(predicciones=repo, lector=lector)
        ).get("/api/predictions").json()

        # Orden descendente por `prediction_time`.
        tiempos = [p["prediction_time"] for p in cuerpo["predicciones"]]
        assert tiempos == sorted(tiempos, reverse=True)
        assert [p["id"] for p in cuerpo["predicciones"]] == ["fila-70", "fila-40", "fila-15"]
        # 20 filas no son 20 consultas: UNA sola para toda la página.
        assert len(lector.consultas_rango) == 1
        assert lector.consultas_ultima_vela == 1  # solo el cierre, para anclar el rango
        # Sesión completa y horizonte cumplido → resultado real en cada fila.
        assert all(
            p["resultado_real"]["estado"] == "DISPONIBLE" for p in cuerpo["predicciones"]
        )
        # La vela 15 cierra en 515 y el horizonte (+3 velas) en 518:
        # +0,582524… %. La banda la pone el modelo, no una constante suelta.
        ultimo = cuerpo["predicciones"][-1]["resultado_real"]
        assert ultimo["retorno_pct"] == pytest.approx((518 - 515) / 515 * 100)
        assert ultimo["etiqueta"] == "UP"
        assert ultimo["banda_pct"] == 0.1

    def test_ultima_fila_de_la_serie_es_pendiente(self) -> None:
        # Predicción en la vela 75: su horizonte (3 velas) no existe todavía.
        repo = PrediccionesFalso()
        repo.filas.append(_fila(75))
        cuerpo = cliente_de_prueba(
            estado_de_prueba(predicciones=repo, lector=LectorFalso(SERIE_COMPLETA))
        ).get("/api/predictions").json()

        resultado = cuerpo["predicciones"][0]["resultado_real"]
        assert resultado["estado"] == "PENDIENTE"
        assert resultado["retorno_pct"] is None

    def test_influx_caído_sirve_historial_con_aviso(self) -> None:
        repo = PrediccionesFalso()
        repo.filas.append(_fila(40))
        estado = estado_de_prueba(
            predicciones=repo, lector=LectorFalso(error=ConnectionError("refused"))
        )
        respuesta = cliente_de_prueba(estado).get("/api/predictions")

        assert respuesta.status_code == 200
        cuerpo = respuesta.json()
        assert len(cuerpo["predicciones"]) == 1
        assert cuerpo["predicciones"][0]["resultado_real"] is None
        assert any("No se pudieron leer los precios históricos" in a for a in cuerpo["avisos"])

    def test_postgres_caído_es_503_en_espanol(self) -> None:
        repo = PrediccionesFalso(error=ConnectionError("refused"))
        respuesta = cliente_de_prueba(
            estado_de_prueba(predicciones=repo)
        ).get("/api/predictions")

        assert respuesta.status_code == 503
        assert respuesta.json()["mensaje"].startswith("Base de datos no disponible")

    def test_limit_y_offset_se_respeta_en_la_respuesta(self) -> None:
        repo = PrediccionesFalso()
        repo.filas.extend([_fila(i) for i in (10, 40, 70, 20, 50)])
        cuerpo = cliente_de_prueba(
            estado_de_prueba(predicciones=repo, lector=LectorFalso(SERIE_COMPLETA))
        ).get("/api/predictions?limit=2&offset=1").json()

        assert [p["id"] for p in cuerpo["predicciones"]] == ["fila-50", "fila-40"]
        assert cuerpo["total"] == 5
        assert cuerpo["limit"] == 2
        assert cuerpo["offset"] == 1

    def test_limit_sobre_200_es_422_en_espanol(self) -> None:
        respuesta = cliente_de_prueba(estado_de_prueba()).get("/api/predictions?limit=500")

        assert respuesta.status_code == 422
        assert respuesta.json()["detail"][0]["mensaje"] == "debe ser menor o igual a 200"


class TestUltimaPrediccion:
    def test_sin_predicciones_encontrada_falsa_sin_senal(self) -> None:
        cuerpo = cliente_de_prueba(estado_de_prueba()).get(
            "/api/predictions/latest"
        ).json()

        assert cuerpo["encontrada"] is False
        assert cuerpo["prediccion"] is None
        assert cuerpo["senal"]["operable"] is False
        assert "Sin predicción registrada" in cuerpo["senal"]["motivo"]
        # El aviso de configuración por defecto viaja en este endpoint (D8).
        assert any("Sin configuración guardada" in a for a in cuerpo["avisos"])

    def test_con_prediccion_trae_senal_y_resultado_real(self) -> None:
        repo = PrediccionesFalso()
        repo.filas.append(_fila(50, tendencia="UP", confianza=0.9))
        cuerpo = cliente_de_prueba(
            estado_de_prueba(predicciones=repo, lector=LectorFalso(SERIE_COMPLETA))
        ).get("/api/predictions/latest").json()

        assert cuerpo["encontrada"] is True
        assert cuerpo["prediccion"]["trend_prediction"] == "UP"
        assert cuerpo["prediccion"]["fecha"]  # DD/MM/YYYY HH:mm:ss ya formateado
        # La señal la decidió el backend con el umbral por defecto (0,75).
        assert cuerpo["senal"] == {
            "operable": True,
            "motivo": (
                "Señal operable: tendencia al alza con confianza 90,0 % "
                "≥ umbral 75 % del perfil Moderado."
            ),
            "umbral": 0.75,
            "perfil": "MODERATE",
            "tendencia": "UP",
            "confianza": 0.9,
        }
        resultado = cuerpo["prediccion"]["resultado_real"]
        assert resultado["estado"] == "DISPONIBLE"
        assert resultado["retorno_pct"] == pytest.approx(3 / 550 * 100)

    def test_influx_caído_no_tumba_la_ultima_prediccion(self) -> None:
        repo = PrediccionesFalso()
        repo.filas.append(_fila(50))
        estado = estado_de_prueba(
            predicciones=repo, lector=LectorFalso(error=ConnectionError("refused"))
        )
        respuesta = cliente_de_prueba(estado).get("/api/predictions/latest")

        assert respuesta.status_code == 200
        cuerpo = respuesta.json()
        assert cuerpo["encontrada"] is True
        assert cuerpo["prediccion"]["resultado_real"] is None
        assert any("No se pudieron leer los precios históricos" in a for a in cuerpo["avisos"])

    def test_postgres_caído_es_503_en_espanol(self) -> None:
        repo = PrediccionesFalso(error=ConnectionError("refused"))
        respuesta = cliente_de_prueba(
            estado_de_prueba(predicciones=repo)
        ).get("/api/predictions/latest")

        assert respuesta.status_code == 503
        assert respuesta.json()["mensaje"].startswith("Base de datos no disponible")


class TestYield:
    def _estado_con(self, velas):
        return estado_de_prueba(lector=LectorFalso(velas))

    def test_subida_se_duplica_y_marca_virtual(self) -> None:
        # Apertura 500, último cierre 529 → +5,8 % → rendimiento 2x = +11,6 %.
        cuerpo = cliente_de_prueba(
            self._estado_con(serie_sesion(APERTURA_MS, 30))
        ).get("/api/yield").json()

        assert cuerpo["virtual"] is True
        assert "sin capital" in cuerpo["nota"]
        assert cuerpo["disponible"] is True
        assert cuerpo["variacion_nasdaq_pct"] == pytest.approx(5.8)
        assert cuerpo["rendimiento_ritech_pct"] == pytest.approx(11.6, rel=1e-9)
        assert cuerpo["limite_perdida_pct"] == 1.25
        assert cuerpo["umbral_perdida_alcanzado"] is False

    def test_bajada_se_pasa_sin_palancar(self) -> None:
        # Apertura 500, último cierre 471 → −5,8 %: la pérdida NO se duplica.
        bajista = [vela(APERTURA_MS + i * INTERVALO_MS, 500.0 - i) for i in range(30)]
        cuerpo = cliente_de_prueba(self._estado_con(bajista)).get("/api/yield").json()

        assert cuerpo["variacion_nasdaq_pct"] == pytest.approx(-5.8)
        assert cuerpo["rendimiento_ritech_pct"] == pytest.approx(-5.8)
        assert cuerpo["umbral_perdida_alcanzado"] is True

    def test_perdida_baja_del_limite_no_dispara_el_umbral(self) -> None:
        # −0,58 % está dentro del −1,25 % permitido (RF-202).
        leve = [vela(APERTURA_MS + i * INTERVALO_MS, 500.0 - i * 0.1) for i in range(30)]
        cuerpo = cliente_de_prueba(self._estado_con(leve)).get("/api/yield").json()

        assert cuerpo["variacion_nasdaq_pct"] == pytest.approx(-0.58)
        assert cuerpo["umbral_perdida_alcanzado"] is False

    def test_influx_caído_es_503_en_espanol(self) -> None:
        estado = estado_de_prueba(lector=LectorFalso(error=ConnectionError("refused")))
        respuesta = cliente_de_prueba(estado).get("/api/yield")

        assert respuesta.status_code == 503
        assert respuesta.json()["mensaje"].startswith("La base de precios no responde")

    def test_serie_vacia_no_inventa_cifras(self) -> None:
        cuerpo = cliente_de_prueba(self._estado_con([])).get("/api/yield").json()

        assert cuerpo["disponible"] is False
        assert cuerpo["motivo"] == "Sin velas registradas en la serie."
        assert cuerpo["variacion_nasdaq_pct"] is None
        assert cuerpo["rendimiento_ritech_pct"] is None
        assert cuerpo["virtual"] is True  # la condición virtual no depende de los datos


class TestCalidadModelo:
    def test_con_m1_viva_expone_los_numeros_del_informe(self) -> None:
        cuerpo = cliente_de_prueba(estado_de_prueba()).get("/api/model-quality").json()

        assert cuerpo["disponible"] is True
        assert cuerpo["model_type"] == "mlp"
        assert cuerpo["macro_f1_test"] == pytest.approx(0.4312686359675911)
        assert cuerpo["entrenado_en"] == fecha_hora(CUERPO_MODELO_EJEMPLO["entrenado_en_ms"])
        assert FORMATO_FECHA.match(cuerpo["entrenado_en"])
        acierto = cuerpo["acierto_direccional"]
        assert acierto["ratio"] == pytest.approx(0.4728888888888889)
        assert acierto["aciertos"] == 532
        assert acierto["n"] == 1125
        assert acierto["fuente"] == "informe_entrenamiento"
        assert acierto["ventaja_demostrada"] is False
        # La nota de honestidad no es opcional: va en cada respuesta.
        assert "sin ventaja direccional" in cuerpo["nota"]

    def test_m1_caída_no_es_error_http(self) -> None:
        estado = estado_de_prueba(
            cliente_ia=ClienteIaFalso(error=ConnectionError("M1 caída"))
        )
        respuesta = cliente_de_prueba(estado).get("/api/model-quality")

        assert respuesta.status_code == 200
        assert respuesta.json()["disponible"] is False
        assert "La IA no responde" in respuesta.json()["motivo"]
        assert "sin ventaja direccional" in respuesta.json()["nota"]

    def test_cuerpo_que_no_cumple_el_contrato_se_informa(self) -> None:
        estado = estado_de_prueba(cliente_ia=ClienteIaFalso(cuerpo_modelo={}))
        cuerpo = cliente_de_prueba(estado).get("/api/model-quality").json()

        assert cuerpo["disponible"] is False
        assert "Respuesta inesperada del servicio de IA" in cuerpo["motivo"]

    def test_sin_servicio_configurado_tambien_responde(self) -> None:
        estado = estado_de_prueba(cliente_ia=ClienteIaFalso())
        estado.cliente_ia = None
        cuerpo = cliente_de_prueba(estado).get("/api/model-quality").json()

        assert cuerpo["disponible"] is False
        assert cuerpo["motivo"] == "Sin servicio de IA configurado."


class TestErroresEnEspañol:
    def test_ruta_desconocida_es_404_en_espanol(self) -> None:
        respuesta = cliente_de_prueba(estado_de_prueba()).get("/api/loquesea")

        assert respuesta.status_code == 404
        assert respuesta.json() == {"mensaje": "Recurso no encontrado."}

    def test_metodo_no_permitido_es_405_en_espanol(self) -> None:
        # La API es SOLO GET: un POST contesta, no ejecuta (RNF-001).
        respuesta = cliente_de_prueba(estado_de_prueba()).post("/api/status")

        assert respuesta.status_code == 405
        assert respuesta.json() == {"mensaje": "Método no permitido."}

    def test_configuraciones_sin_fila_y_estado_sin_velas_no_rompen(self) -> None:
        # Configuración por defecto + serie vacía: respuesta completa, en
        # español y con los avisos correspondientes, no un 500.
        estado = estado_de_prueba(configuraciones=ConfiguracionesFalso())
        respuesta = cliente_de_prueba(estado).get("/api/status")

        assert respuesta.status_code == 200
        cuerpo = respuesta.json()
        assert cuerpo["estado"] == "AMARILLO"
        assert "Revisar el sistema" in cuerpo["motivo"]
        assert "sin velas registradas en horario de mercado" in cuerpo["motivo"]
        assert cuerpo["ultima_vela"] is None
