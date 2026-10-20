"""Dominio → modelos de salida: un solo lugar donde se formatea lo que sale.

Ningún endpoint escribe fechas ni números a mano: aquí se centraliza para que
`DD/MM/YYYY HH:mm:ss` (SRS §5.6) y los redondeos sean idénticos en el estado,
el gráfico, el historial y el rendimiento.
"""

from __future__ import annotations

from src.database.predictions import PrediccionGuardada
from src.endpoints.esquemas import (
    PrediccionSalida,
    ResultadoRealSalida,
    SenalSalida,
    VelaResumenSalida,
    VelaSalida,
)
from src.ingestion.engine import NasdaqCandle
from src.security.perfiles import Senal
from src.utils.fechas import fecha_hora
from src.utils.resultados import ResultadoReal


def a_vela_salida(vela: NasdaqCandle) -> VelaSalida:
    return VelaSalida(
        timestamp=vela.timestamp,
        fecha=fecha_hora(vela.timestamp),
        open=vela.price_open,
        high=vela.price_high,
        low=vela.price_low,
        close=vela.price_close,
        volume=vela.volume,
    )


def a_resumen_salida(
    vela: NasdaqCandle | None, *, ahora_ms: int | None = None, precio: float | None = None
) -> VelaResumenSalida | None:
    if vela is None:
        return None
    base = precio if precio is not None else vela.price_close
    antiguedad = None
    if ahora_ms is not None:
        antiguedad = max(0, int((ahora_ms - vela.timestamp) / 1000))
    return VelaResumenSalida(
        timestamp=vela.timestamp,
        fecha=fecha_hora(vela.timestamp),
        precio=base,
        antiguedad_s=antiguedad,
    )


def a_senal_salida(senal: Senal) -> SenalSalida:
    return SenalSalida(
        operable=senal.operable,
        motivo=senal.motivo,
        umbral=senal.umbral,
        perfil=senal.perfil,
        tendencia=senal.tendencia,
        confianza=senal.confianza,
    )


def a_resultado_salida(resultado: ResultadoReal | None) -> ResultadoRealSalida | None:
    if resultado is None:
        return None
    return ResultadoRealSalida(
        estado=resultado.estado,
        motivo=resultado.motivo,
        retorno_pct=resultado.retorno_pct,
        etiqueta=resultado.etiqueta,
        banda_pct=resultado.banda_pct,
    )


def a_prediccion_salida(
    prediccion: PrediccionGuardada, resultado: ResultadoReal | None = None
) -> PrediccionSalida:
    return PrediccionSalida(
        id=prediccion.id,
        prediction_time=prediccion.prediction_time,
        fecha=fecha_hora(prediccion.prediction_time),
        trend_prediction=prediccion.trend_prediction,
        confidence_score=prediccion.confidence_score,
        technical_rationale=prediccion.technical_rationale,
        resultado_real=a_resultado_salida(resultado),
    )
