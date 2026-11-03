"""`GET /api/yield` — rendimiento **virtual** 2x frente a la apertura de sesión.

RF-201 sin capital: la cifra se calcula sobre la serie histórica y no corresponde
a ninguna orden. Por eso la respuesta lleva `virtual: true` y una nota
explícita en cada respuesta — la UI debe poder mostrar «virtual, sin capital»
sin tener que interpretar nada.

La variación se toma de la **apertura de la sesión en curso** (primera vela del
último bloque de velas) al último cierre, y el rendimiento sale del
`AsymmetricCalculator` real, no de una reimplementación: 2x en subidas y
passthrough en bajadas (RF-201/RF-202), con el umbral de −1,25 % evaluado con
`is_threshold_breached`.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from src.ai.features import Candle, apertura_de_sesion
from src.ai.prediccion import VELAS_A_CONSULTAR, a_candle
from src.calculator.asymmetric import AsymmetricCalculator
from src.database.influx_lectura import LectorVelas
from src.endpoints.convertidores import a_resumen_salida
from src.endpoints.dependencias import EstadoAplicacion, estado_app
from src.endpoints.esquemas import RendimientoSalida
from src.ingestion.engine import NasdaqCandle

logger = logging.getLogger(__name__)

router = APIRouter()

NOTA = (
    "Rendimiento virtual calculado sobre los precios históricos: sin capital en "
    "riesgo y sin órdenes enviadas (Modo Asesor)."
)


def _lector(estado: EstadoAplicacion = Depends(estado_app)) -> LectorVelas:
    return estado.lector


def _sin_datos(motivo: str) -> RendimientoSalida:
    return RendimientoSalida(
        virtual=True,
        nota=NOTA,
        disponible=False,
        motivo=motivo,
        apertura_sesion=None,
        ultima_vela=None,
        variacion_nasdaq_pct=None,
        rendimiento_ritech_pct=None,
        limite_perdida_pct=AsymmetricCalculator.MAX_LOSS_PCT,
        umbral_perdida_alcanzado=False,
    )


@router.get("/api/yield", response_model=RendimientoSalida, tags=["rendimiento"])
def rendimiento(estado: EstadoAplicacion = Depends(estado_app)) -> RendimientoSalida:
    """Variación del NASDAQ y rendimiento asimétrico de la sesión actual."""
    lector = estado.lector
    try:
        ultima = lector.ultima_vela()
        serie = lector.ultimas_velas(VELAS_A_CONSULTAR)
    except Exception as exc:  # noqa: BLE001 — InfluxDB inaccesible → 503 en español
        raise HTTPException(
            status_code=503, detail=f"La base de precios no responde: {exc}"
        ) from exc

    if ultima is None or not serie:
        return _sin_datos("Sin velas registradas en la serie.")

    # Una sola función con el disparo automático del Kill-Switch: si el panel
    # y el motor usaran bases distintas, nadie sabría cuál tiene razón.
    primera = apertura_de_sesion([a_candle(v) for v in serie])
    if primera is None:
        return _sin_datos("Sin velas de la sesión para calcular la apertura.")
    apertura = _a_mas_antigua(serie, [primera])
    base = apertura.price_open
    if base <= 0:
        return _sin_datos("La apertura de la sesión no es un precio válido.")

    variacion = (ultima.price_close - base) / base * 100.0
    rendi = AsymmetricCalculator.calculate_yield(variacion)
    umbral_alcanzado = AsymmetricCalculator.is_threshold_breached(base, ultima.price_close)

    return RendimientoSalida(
        virtual=True,
        nota=NOTA,
        disponible=True,
        motivo=None,
        apertura_sesion=a_resumen_salida(apertura, precio=base),
        ultima_vela=a_resumen_salida(
            ultima, ahora_ms=int(estado.ahora().timestamp() * 1000)
        ),
        variacion_nasdaq_pct=variacion,
        rendimiento_ritech_pct=rendi,
        limite_perdida_pct=AsymmetricCalculator.MAX_LOSS_PCT,
        umbral_perdida_alcanzado=umbral_alcanzado,
    )


def _a_mas_antigua(serie: list[NasdaqCandle], sesion: list[Candle]) -> NasdaqCandle:
    """Recupera la vela almacenada correspondiente a la primera de la sesión."""
    primero = sesion[0]
    for vela in serie:
        if vela.timestamp == primero.timestamp:
            return vela
    raise RuntimeError("la apertura de la sesión no está en la serie consultada")
