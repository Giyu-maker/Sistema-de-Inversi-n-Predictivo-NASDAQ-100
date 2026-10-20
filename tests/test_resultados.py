"""Resultado real de una predicción — los tres estados (decisión D6).

* `PENDIENTE`: la predicción está entre las 3 últimas velas de la serie.
* `NO_APLICA`: el horizonte de 15 min se sale de la sesión (o faltan datos
  para medirlo) y no hay precio con qué comparar.
* `DISPONIBLE`: hay retorno y etiqueta, con la misma banda del modelo.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.ai.features import LABEL_NAMES, Candle
from src.ai.prediccion import a_candle
from src.ingestion.engine import NasdaqCandle
from src.utils.fechas import ZONA_MERCADO
from src.utils.resultados import (
    ESTADO_DISPONIBLE,
    ESTADO_NO_APLICA,
    ESTADO_PENDIENTE,
    resultado_real,
)

#: Apertura de la sesión del viernes 16/10/2026 (09:30 ET).
APERTURA_VIERNES = int(
    datetime(2026, 10, 16, 9, 30, tzinfo=ZONA_MERCADO).timestamp() * 1000
)
#: La sesión siguiente (lunes 19/10/2026).
APERTURA_LUNES = int(
    datetime(2026, 10, 19, 9, 30, tzinfo=ZONA_MERCADO).timestamp() * 1000
)
INTERVALO_MS = 5 * 60 * 1000
BANDA = 0.1


def _sesion(inicio_ms: int, precios: list[float]) -> list[NasdaqCandle]:
    """Velas con open=high=low=close: el precio que se pide es el que hay."""
    return [
        NasdaqCandle(
            timestamp=inicio_ms + i * INTERVALO_MS,
            price_open=precio,
            price_high=precio,
            price_low=precio,
            price_close=precio,
            volume=1_000.0,
        )
        for i, precio in enumerate(precios)
    ]


def _candles(velas: list[NasdaqCandle]) -> list[Candle]:
    return [a_candle(v) for v in velas]


class TestPendiente:
    @pytest.mark.parametrize("desde_el_final", [0, 1, 2])
    def test_las_tres_ultimas_velas_siguen_pendientes(self, desde_el_final: int) -> None:
        serie = _sesion(APERTURA_VIERNES, [500.0] * 78)
        ts = serie[-1 - desde_el_final].timestamp
        resultado = resultado_real(
            ts, _candles(serie), banda_pct=BANDA, ultima_vela_ms=serie[-1].timestamp
        )
        assert resultado.estado == ESTADO_PENDIENTE
        assert resultado.retorno_pct is None and resultado.etiqueta is None
        assert "15 min" in resultado.motivo

    def test_la_cuarta_desde_el_final_deja_de_estar_pendiente(self) -> None:
        serie = _sesion(APERTURA_VIERNES, [500.0] * 78)
        resultado = resultado_real(
            serie[-4].timestamp,
            _candles(serie),
            banda_pct=BANDA,
            ultima_vela_ms=serie[-1].timestamp,
        )
        assert resultado.estado != ESTADO_PENDIENTE

    def test_sin_velas_en_la_serie_todo_esta_pendiente(self) -> None:
        assert (
            resultado_real(
                APERTURA_VIERNES, [], banda_pct=BANDA, ultima_vela_ms=None
            ).estado
            == ESTADO_PENDIENTE
        )


class TestDisponible:
    def test_calcula_el_retorno_desde_el_cierre_de_la_ventana(self) -> None:
        # Índice 30 = cierre de la ventana; el horizonte son los tres
        # siguientes. Precios puestos a mano para que el retorno sea +2,00 %.
        precios = [500.0] * 78
        precios[33] = 510.0
        serie = _sesion(APERTURA_VIERNES, precios)
        resultado = resultado_real(
            serie[30].timestamp,
            _candles(serie),
            banda_pct=BANDA,
            ultima_vela_ms=serie[-1].timestamp,
        )
        assert resultado.estado == ESTADO_DISPONIBLE
        assert resultado.retorno_pct == pytest.approx((510.0 - 500.0) / 500.0 * 100.0)
        assert resultado.etiqueta == "UP"
        assert resultado.banda_pct == BANDA
        assert "completo" in resultado.motivo

    def test_movimiento_dentro_de_la_banda_es_stable(self) -> None:
        # 500 → 500,30 = +0,06 % < banda de 0,1 %: el modelo lo rotula STABLE y
        # el resultado real debe rotularlo igual, con la misma banda.
        precios = [500.0] * 78
        precios[33] = 500.30
        serie = _sesion(APERTURA_VIERNES, precios)
        resultado = resultado_real(
            serie[30].timestamp,
            _candles(serie),
            banda_pct=BANDA,
            ultima_vela_ms=serie[-1].timestamp,
        )
        assert resultado.estado == ESTADO_DISPONIBLE
        assert resultado.retorno_pct == pytest.approx(0.06)
        assert resultado.etiqueta == LABEL_NAMES[1]  # STABLE

    def test_caida_fuerte_es_down(self) -> None:
        precios = [500.0] * 78
        precios[33] = 480.0
        serie = _sesion(APERTURA_VIERNES, precios)
        resultado = resultado_real(
            serie[30].timestamp,
            _candles(serie),
            banda_pct=BANDA,
            ultima_vela_ms=serie[-1].timestamp,
        )
        assert resultado.estado == ESTADO_DISPONIBLE
        assert resultado.etiqueta == "DOWN"


class TestNoAplica:
    def test_el_horizonte_que_cruza_el_cierre_no_aplica(self) -> None:
        # Predicción a las 15:55 del viernes, con la serie ya avanzada al
        # lunes: la predicción NO está entre las tres últimas velas, pero sus
        # 15 minutos siguientes no existen porque el mercado cerró. Es el caso
        # que separa PENDIENTE de NO_APLICA.
        viernes = _sesion(APERTURA_VIERNES, [500.0] * 78)
        lunes = _sesion(APERTURA_LUNES, [505.0] * 78)
        serie = viernes + lunes
        ts = viernes[-1].timestamp  # 15:55 ET del viernes
        resultado = resultado_real(
            ts, _candles(serie), banda_pct=BANDA, ultima_vela_ms=serie[-1].timestamp
        )
        assert resultado.estado == ESTADO_NO_APLICA
        assert "fuera de la sesión" in resultado.motivo

    def test_la_vela_de_la_prediccion_no_esta_en_el_rango(self) -> None:
        serie = _sesion(APERTURA_VIERNES, [500.0] * 78)
        fuera = serie[0].timestamp + INTERVALO_MS * 500
        resultado = resultado_real(
            fuera, _candles(serie), banda_pct=BANDA, ultima_vela_ms=serie[-1].timestamp
        )
        assert resultado.estado == ESTADO_NO_APLICA

    def test_sin_velas_previas_no_se_puede_medir_el_retorno(self) -> None:
        # Rango que empieza en la propia predicción: no hay ventana de 12 velas
        # antes, y sin ella el retorno no sería honesto.
        serie = _sesion(APERTURA_VIERNES, [500.0] * 78)
        recortadas = _candles(serie[30:])
        resultado = resultado_real(
            serie[30].timestamp,
            recortadas,
            banda_pct=BANDA,
            ultima_vela_ms=serie[-1].timestamp,
        )
        assert resultado.estado == ESTADO_NO_APLICA
        assert "anteriores" in resultado.motivo
