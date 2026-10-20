"""Ciclo de predicción de M2 — ventana → inferencia → `ai_predictions`.

Es el tramo que el backlog dejó pendiente desde el Sprint 2: sin él, el
scheduler escribía velas y la IA no participaba en nada. Aquí se prueba que:

* no se predice con una ventana incompleta ni se llama a M1 por inercia;
* si la IA no responde, no se persiste absolutamente nada;
* lo que se persiste usa el cierre de la última vela como `prediction_time`.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from src.ai.client import ErrorDeInferencia
from src.ai.features import WINDOW
from src.ai.prediccion import (
    VELAS_A_CONSULTAR,
    a_candle,
    predecir_y_persistir,
    ultima_ventana,
)
from src.database.predictions import PrediccionGuardada, PrediccionNueva
from src.ingestion.scheduler import IngestionScheduler
from tests.fakes import (
    APERTURA_MS,
    ClienteInferenciaFalso,
    INTERVALO_MS,
    LectorFalso,
    PrediccionesFalso,
    prediccion_falsa,
    serie_sesion,
)


class TestUltimaVentana:
    def test_serie_vacia_no_da_ventana(self) -> None:
        assert ultima_ventana([]) is None

    def test_sesion_mas_corta_que_la_ventana_no_da_ventana(self) -> None:
        corta = [a_candle(v) for v in serie_sesion(APERTURA_MS, WINDOW - 1)]
        assert ultima_ventana(corta) is None

    def test_toma_las_ultimas_velas_de_la_ultima_sesion(self) -> None:
        ayer = serie_sesion(APERTURA_MS, 40)
        hoy = serie_sesion(APERTURA_MS + INTERVALO_MS * 24 * 12, 30)
        preparada = ultima_ventana([a_candle(v) for v in ayer + hoy])
        assert preparada is not None
        assert len(preparada.ventana) == WINDOW
        assert preparada.velas_en_sesion == 30
        # El `prediction_time` es el cierre de la última vela, no el reloj (D2).
        assert preparada.cierre == hoy[-1].timestamp
        assert preparada.ventana[-1].timestamp == preparada.cierre

    def test_no_mezcla_dos_sesiones_en_la_ventana(self) -> None:
        ayer = serie_sesion(APERTURA_MS, 40)
        hoy = serie_sesion(APERTURA_MS + INTERVALO_MS * 24 * 12, 5)
        preparada = ultima_ventana([a_candle(v) for v in ayer + hoy])
        assert preparada is None  # la sesión de hoy aún no completa 12 velas


class _RepoEnConflicto(PrediccionesFalso):
    """`ultima()` da una fila vieja y `guardar` devuelve None (índice único)."""

    def guardar(self, pred: PrediccionNueva):
        self.intentos.append(pred)
        return None


class TestPredecirYPersistir:
    def _velas(self, n: int = 30):
        return serie_sesion(APERTURA_MS, n)

    def test_persiste_con_el_cierre_de_la_ventana(self) -> None:
        repo = PrediccionesFalso()
        cliente = ClienteInferenciaFalso(prediccion_falsa("UP", 0.81))
        velas = self._velas()

        resultado = predecir_y_persistir(velas, cliente, repo)

        assert resultado is not None and resultado.nueva is True
        assert len(repo.intentos) == 1
        guardada: PrediccionNueva = repo.intentos[0]
        assert guardada.prediction_time == velas[-1].timestamp
        assert guardada.trend_prediction == "UP"
        assert guardada.confidence_score == pytest.approx(0.81)
        assert guardada.technical_rationale.strip() != ""
        # M1 recibió exactamente una ventana de (WINDOW, N_FEATURES).
        assert len(cliente.ventanas_recibidas) == 1
        assert cliente.ventanas_recibidas[0].shape[0] == WINDOW

    def test_si_ia_no_responde_no_persiste_nada(self) -> None:
        repo = PrediccionesFalso()
        cliente = ClienteInferenciaFalso(error=ErrorDeInferencia("M1 caído"))

        assert predecir_y_persistir(self._velas(), cliente, repo) is None
        assert repo.intentos == []
        assert repo.filas == []

    def test_ventana_incompleta_no_llama_a_m1(self) -> None:
        repo = PrediccionesFalso()
        cliente = ClienteInferenciaFalso()

        assert predecir_y_persistir(serie_sesion(APERTURA_MS, 5), cliente, repo) is None
        assert cliente.ventanas_recibidas == []
        assert repo.intentos == []

    def test_con_el_mercado_cerrado_no_vuelve_a_llamar_a_m1(self) -> None:
        # Misma vela que la última predicción = misma ventana = misma predicción.
        repo = PrediccionesFalso()
        cliente = ClienteInferenciaFalso()
        velas = self._velas()

        primera = predecir_y_persistir(velas, cliente, repo)
        segunda = predecir_y_persistir(velas, cliente, repo)

        assert primera is not None and primera.nueva is True
        assert segunda is not None and segunda.nueva is False
        assert len(cliente.ventanas_recibidas) == 1  # la segunda no gastó llamada
        assert len(repo.intentos) == 1

    def test_si_el_indice_unico_descarta_la_fila_recupera_la_existente(self) -> None:
        # Caso de dos procesos a la vez: `ultima()` aún no la ve pero el
        # índice único ya la descartó. No debe devolver None ni perder el
        # resultado del ciclo.
        repo = _RepoEnConflicto()
        repo.filas.append(
            PrediccionGuardada(
                id="existente",
                prediction_time=1,
                trend_prediction="DOWN",
                confidence_score=0.9,
                technical_rationale="ya estaba",
            )
        )
        cliente = ClienteInferenciaFalso()
        velas = self._velas()

        resultado = predecir_y_persistir(velas, cliente, repo)

        assert resultado is not None and resultado.nueva is False
        assert resultado.prediccion.id == "existente"

    def test_postgres_caído_propaga_para_que_el_scheduler_lo_registre(self) -> None:
        # El ciclo de IA no tiene por qué tragarse el fallo de PostgreSQL: lo
        # propaga y el scheduler (test de abajo) lo convierte en log.
        repo = PrediccionesFalso(error=RuntimeError("pg caído"))
        cliente = ClienteInferenciaFalso()
        with pytest.raises(RuntimeError, match="pg caído"):
            predecir_y_persistir(self._velas(), cliente, repo)


class TestSchedulerPasoDeIA:
    def test_constructor_a_medio_cablear_es_error(self) -> None:
        with pytest.raises(ValueError, match="completo o no se configura"):
            IngestionScheduler(
                engine=None,  # type: ignore[arg-type]
                calculator=None,  # type: ignore[arg-type]
                lector=LectorFalso(),
            )

    def test_sin_colaboradores_es_el_ciclo_del_sprint_2(self) -> None:
        # Debe seguir funcionando (y decirlo), no romper: es el walking
        # skeleton con el que se cerró el Sprint 2.
        scheduler = IngestionScheduler(engine=None, calculator=None)  # type: ignore[arg-type]
        assert scheduler.lector is None
        scheduler._predecir()  # no hace nada, no explota

    def test_lector_caído_no_tumba_el_ciclo(self) -> None:
        scheduler = IngestionScheduler(
            engine=None,  # type: ignore[arg-type]
            calculator=None,  # type: ignore[arg-type]
            lector=LectorFalso(error=ConnectionError("influx caído")),
            cliente=Mock(),
            predicciones=PrediccionesFalso(),
        )
        scheduler._predecir()  # registra el error y continúa, no propaga

    def test_usa_el_lector_para_traer_las_velas_de_la_sesion(self) -> None:
        lector = LectorFalso(serie_sesion(APERTURA_MS, 30))
        scheduler = IngestionScheduler(
            engine=None,  # type: ignore[arg-type]
            calculator=None,  # type: ignore[arg-type]
            lector=lector,
            cliente=Mock(),
            predicciones=PrediccionesFalso(),
        )
        with patch("src.ingestion.scheduler.predecir_y_persistir") as ciclo:
            scheduler._predecir()
        assert lector.consultas_ultimas == [VELAS_A_CONSULTAR]
        assert ciclo.call_count == 1

    def test_un_fallo_del_ciclo_de_ia_tampoco_detiene_la_cadencia(self) -> None:
        scheduler = IngestionScheduler(
            engine=None,  # type: ignore[arg-type]
            calculator=None,  # type: ignore[arg-type]
            lector=LectorFalso(serie_sesion(APERTURA_MS, 30)),
            cliente=Mock(),
            predicciones=PrediccionesFalso(),
        )
        with patch(
            "src.ingestion.scheduler.predecir_y_persistir",
            side_effect=RuntimeError("algo inesperado"),
        ):
            scheduler._predecir()  # no propaga
