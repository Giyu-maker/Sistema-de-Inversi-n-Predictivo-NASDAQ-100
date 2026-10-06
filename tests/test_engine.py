"""Pruebas del IngestionEngine — RF-101/RF-102/RF-103 en formato canónico.

Sin red y sin InfluxDB real: proveedor sintético + mocks (`unittest.mock`).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from src.ingestion.engine import IngestionEngine, NasdaqCandle, parse_raw_candle
from src.ingestion.providers import SyntheticMarketDataProvider, TwelveDataProvider

_NY = ZoneInfo("America/New_York")

_RAW_OK: dict[str, Any] = {
    "open": 100.0, "high": 101.5, "low": 99.5, "close": 101.0, "volume": 900.0,
}


def _engine(provider: Any = None) -> IngestionEngine:
    return IngestionEngine(
        influx_client=MagicMock(), api_key="clave-de-prueba", provider=provider
    )


class TestParseRawCandle:
    def test_convierte_vela_canonica_a_epoch_ms_en_hora_de_nueva_york(self) -> None:
        candle = parse_raw_candle("2026-10-06 09:35:00", _RAW_OK)
        esperado = int(datetime(2026, 10, 6, 9, 35, tzinfo=_NY).timestamp() * 1000)
        assert candle.timestamp == esperado
        # La hora naive se interpreta como Nueva York, no como UTC:
        assert candle.timestamp != int(datetime(2026, 10, 6, 9, 35, tzinfo=ZoneInfo("UTC")).timestamp() * 1000)

    def test_valor_conocido_verificado_contra_la_api(self) -> None:
        # Verificación previa del Sprint 2 (23/09/2026 09:30 EDT → epoch ms):
        assert parse_raw_candle("2026-09-23 09:30:00", _RAW_OK).timestamp == 1790170200000

    def test_convierte_strings_a_float(self) -> None:
        raw: dict[str, Any] = {k: str(v) for k, v in _RAW_OK.items()}
        candle = parse_raw_candle("2026-10-06 09:35:00", raw)
        assert candle.price_open == 100.0
        assert candle.volume == 900.0

    @pytest.mark.parametrize(
        "mutacion",
        [
            {"close": None},          # nulo
            {"close": "abc"},         # no numérico
            {"high": -1.0},           # negativo (Pydantic lo rechaza)
        ],
        ids=["nulo", "no-numerico", "negativo"],
    )
    def test_vela_invalida_lanza_value_error(self, mutacion: dict[str, Any]) -> None:
        raw = {**_RAW_OK, **mutacion}
        with pytest.raises(ValueError):
            parse_raw_candle("2026-10-06 09:35:00", raw)

    def test_campo_faltante_lanza_value_error(self) -> None:
        raw = dict(_RAW_OK)
        del raw["low"]
        with pytest.raises(ValueError):
            parse_raw_candle("2026-10-06 09:35:00", raw)

    def test_timestamp_con_formato_invalido_lanza_value_error(self) -> None:
        with pytest.raises(ValueError):
            parse_raw_candle("06/10/2026 09:35", _RAW_OK)


class TestNasdaqCandle:
    def test_rechaza_campos_extra(self) -> None:
        with pytest.raises(ValidationError):
            NasdaqCandle(timestamp=1, price_open=1.0, price_high=1.0,
                         price_low=1.0, price_close=1.0, volume=1.0, extra=1.0)

    def test_rechaza_coercion_de_tipos(self) -> None:
        with pytest.raises(ValidationError):
            NasdaqCandle(timestamp=1, price_open="1.0", price_high=1.0,
                         price_low=1.0, price_close=1.0, volume=1.0)


class TestValidatePayload:
    def test_payload_del_proveedor_sintetico_es_valido(self) -> None:
        engine = _engine(SyntheticMarketDataProvider())
        payload = asyncio.run(engine.fetch_nasdaq_quote())
        assert engine.validate_payload(payload) is True

    def test_payload_vacio_es_invalido(self) -> None:
        assert _engine().validate_payload({}) is False

    def test_error_de_proveedor_es_invalido(self) -> None:
        engine = _engine(SyntheticMarketDataProvider(error="cuota agotada"))
        payload = asyncio.run(engine.fetch_nasdaq_quote())
        assert payload == {"error": "cuota agotada"}
        assert engine.validate_payload(payload) is False

    def test_sin_clave_de_velas_es_invalido(self) -> None:
        assert _engine().validate_payload({"meta": {"symbol": "QQQ"}}) is False

    def test_velas_vacias_son_invalidas(self) -> None:
        assert _engine().validate_payload({"candles": {}}) is False

    def test_fila_no_objeto_es_invalida(self) -> None:
        assert _engine().validate_payload({"candles": {"2026-10-06 09:35:00": 5}}) is False

    def test_valor_nulo_en_fila_es_invalido(self) -> None:
        payload = {"candles": {"2026-10-06 09:35:00": {**_RAW_OK, "close": None}}}
        assert _engine().validate_payload(payload) is False

    def test_valor_negativo_en_fila_es_invalido(self) -> None:
        payload = {"candles": {"2026-10-06 09:35:00": {**_RAW_OK, "volume": -10.0}}}
        assert _engine().validate_payload(payload) is False


class TestDelegacionEnProveedor:
    def test_fetch_delega_en_el_proveedor_inyectado(self) -> None:
        proveedor = MagicMock()
        proveedor.fetch_candles = AsyncMock(return_value={"candles": {"ts": _RAW_OK}})
        engine = _engine(proveedor)
        assert asyncio.run(engine.fetch_nasdaq_quote()) == {"candles": {"ts": _RAW_OK}}
        proveedor.fetch_candles.assert_awaited_once()

    def test_proveedor_por_defecto_es_twelve_data(self) -> None:
        engine = IngestionEngine(influx_client=MagicMock(), api_key="k")
        assert isinstance(engine.provider, TwelveDataProvider)
        assert engine.provider.symbol == IngestionEngine.SYMBOL


class TestWriteToInflux:
    def test_escribe_un_punto_en_el_bucket_configurado(self) -> None:
        influx = MagicMock()
        engine = IngestionEngine(influx_client=influx, api_key="k",
                                 provider=SyntheticMarketDataProvider())
        asyncio.run(engine.write_to_influx(1790170200000, 100.0, 101.0, 900.0))
        influx.write_api.return_value.write.assert_called_once()
        _, kwargs = influx.write_api.return_value.write.call_args
        assert kwargs["bucket"] == "nasdaq_ticks"
        assert kwargs["org"] == "ritech"
