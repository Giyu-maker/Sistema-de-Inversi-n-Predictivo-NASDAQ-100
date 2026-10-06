"""Pruebas de los proveedores de datos — contrato `MarketDataProvider` (RF-101/RF-102).

`TwelveDataProvider` se prueba aquí solo en su normalización (sin red): la
corrida contra la API real es una verificación manual/ puntual, no parte de
pytest, para no consumir la cuota diaria de 800 créditos.
"""

from __future__ import annotations

import asyncio
from typing import Any

from src.ingestion.providers import (
    MarketDataProvider,
    SyntheticMarketDataProvider,
    TwelveDataProvider,
)

# Respuesta nativa de ejemplo de Twelve Data (el más reciente primero).
_NATIVE_OK: dict[str, Any] = {
    "meta": {"symbol": "QQQ", "interval": "5min", "timezone": "America/New_York"},
    "status": "ok",
    "values": [
        {
            "datetime": "2026-10-06 09:35:00", "open": "101.0", "high": "102.5",
            "low": "100.5", "close": "102.0", "volume": "1000",
        },
        {
            "datetime": "2026-10-06 09:30:00", "open": "100.0", "high": "101.5",
            "low": "99.5", "close": "101.0", "volume": "900",
        },
    ],
}


class TestTwelveDataNormalize:
    def test_respuesta_exitosa_se_normaliza_a_formato_canonico(self) -> None:
        resultado = TwelveDataProvider._normalize(_NATIVE_OK)
        assert resultado == {
            "candles": {
                "2026-10-06 09:35:00": {
                    "open": 101.0, "high": 102.5, "low": 100.5,
                    "close": 102.0, "volume": 1000.0,
                },
                "2026-10-06 09:30:00": {
                    "open": 100.0, "high": 101.5, "low": 99.5,
                    "close": 101.0, "volume": 900.0,
                },
            }
        }

    def test_error_de_cuota_se_convierte_en_clave_error(self) -> None:
        nativo = {"code": 429, "message": "You have reached the rate limit of 800 requests per day."}
        assert TwelveDataProvider._normalize(nativo) == {
            "error": "You have reached the rate limit of 800 requests per day."
        }

    def test_values_vacio_produce_payload_sin_velas(self) -> None:
        nativo = {"meta": {"symbol": "QQQ"}, "status": "ok", "values": []}
        assert TwelveDataProvider._normalize(nativo) == {"candles": {}}

    def test_fila_malformada_invalida_el_payload_completo(self) -> None:
        nativo = {
            "values": [
                {"datetime": "2026-10-06 09:35:00", "open": "1", "high": "2",
                 "low": "0.5", "volume": "10"},  # falta "close"
            ]
        }
        resultado = TwelveDataProvider._normalize(nativo)
        assert "error" in resultado
        assert "Vela malformada" in resultado["error"]

    def test_payload_inesperado_produce_clave_error(self) -> None:
        assert "error" in TwelveDataProvider._normalize({"foo": "bar"})


class TestSyntheticMarketDataProvider:
    def test_entrega_velas_canonicas_por_defecto(self) -> None:
        payload = asyncio.run(SyntheticMarketDataProvider().fetch_candles())
        assert set(payload) == {"candles"}
        assert len(payload["candles"]) == 2
        primera = next(iter(payload["candles"].values()))
        assert set(primera) == {"open", "high", "low", "close", "volume"}

    def test_no_acopla_referencias_entre_llamadas(self) -> None:
        proveedor = SyntheticMarketDataProvider()
        primero = asyncio.run(proveedor.fetch_candles())
        primero["candles"].clear()
        segundo = asyncio.run(proveedor.fetch_candles())
        assert segundo["candles"]  # intacto tras mutar el primero

    def test_modo_error(self) -> None:
        proveedor = SyntheticMarketDataProvider(error="clave inválida")
        assert asyncio.run(proveedor.fetch_candles()) == {"error": "clave inválida"}


class TestContratoMarketDataProvider:
    def test_ambos_proveedores_satisfacen_la_interfaz(self) -> None:
        assert isinstance(SyntheticMarketDataProvider(), MarketDataProvider)
        assert isinstance(
            TwelveDataProvider(api_key="k", symbol="QQQ"), MarketDataProvider
        )
