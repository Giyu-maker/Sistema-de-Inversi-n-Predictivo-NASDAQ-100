"""Pruebas del orquestador — RF-101 (cadencia de 5 minutos) del walking skeleton.

Usan mocks (unittest.mock): sin InfluxDB, sin red y sin dependencias extra.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from src.ingestion.engine import parse_raw_candle
from src.ingestion.scheduler import IngestionScheduler, _sorted_candles, _variation_pct

_LATEST_RAW = {
    "1. open": "101.0", "2. high": "102.5", "3. low": "100.5",
    "4. close": "102.0", "5. volume": "1000",
}
_PREVIOUS_RAW = {
    "1. open": "100.0", "2. high": "101.5", "3. low": "99.5",
    "4. close": "101.0", "5. volume": "900",
}


def _payload() -> dict[str, Any]:
    # Orden de inserción invertido a propósito: 09:35 aparece antes que 09:30
    return {"Time Series (5min)": {
        "2026-10-06 09:35:00": _LATEST_RAW,
        "2026-10-06 09:30:00": _PREVIOUS_RAW,
    }}


def _engine(payload: dict[str, Any], valid: bool = True) -> Mock:
    engine = Mock()
    engine.fetch_nasdaq_quote = AsyncMock(return_value=payload)
    engine.validate_payload = Mock(return_value=valid)
    engine.write_to_influx = AsyncMock()
    return engine


def _calculator() -> Mock:
    calculator = Mock()
    calculator.calculate_yield.return_value = 2.0
    calculator.is_market_gap.return_value = False
    return calculator


def test_run_once_persiste_ultima_vela_y_calcula_variacion() -> None:
    engine, calculator = _engine(_payload()), _calculator()
    scheduler = IngestionScheduler(engine, calculator)  # type: ignore[arg-type]

    asyncio.run(scheduler.run_once())

    expected_ts = parse_raw_candle("2026-10-06 09:35:00", _LATEST_RAW).timestamp
    engine.write_to_influx.assert_awaited_once_with(expected_ts, 101.0, 102.0, 1000.0)
    expected_variation = ((102.0 - 101.0) / 101.0) * 100
    calculator.calculate_yield.assert_called_once()
    assert calculator.calculate_yield.call_args.args[0] == expected_variation
    calculator.is_market_gap.assert_called_once()


def test_run_once_payload_invalido_no_escribe_ni_calcula() -> None:
    engine, calculator = _engine(_payload(), valid=False), _calculator()
    scheduler = IngestionScheduler(engine, calculator)  # type: ignore[arg-type]

    asyncio.run(scheduler.run_once())

    engine.write_to_influx.assert_not_awaited()
    calculator.calculate_yield.assert_not_called()


def test_run_once_con_gap_dispara_el_aviso() -> None:
    engine, calculator = _engine(_payload()), _calculator()
    calculator.calculate_yield.return_value = -2.5
    calculator.is_market_gap.return_value = True
    calculator.gap_loss_notice.return_value = "GAP DE MERCADO: aviso de prueba"
    scheduler = IngestionScheduler(engine, calculator)  # type: ignore[arg-type]

    asyncio.run(scheduler.run_once())

    calculator.gap_loss_notice.assert_called_once_with(((102.0 - 101.0) / 101.0) * 100)


def test_run_respeta_la_cadencia_en_varias_iteraciones() -> None:
    engine, calculator = _engine(_payload()), _calculator()
    scheduler = IngestionScheduler(engine, calculator, interval_s=300.0)  # type: ignore[arg-type]

    with patch("src.ingestion.scheduler.asyncio.sleep", new_callable=AsyncMock) as sleep:
        asyncio.run(scheduler.run(max_iterations=3))

    assert engine.fetch_nasdaq_quote.await_count == 3
    assert engine.write_to_influx.await_count == 3
    assert sleep.await_count == 3
    assert all(call.args == (300.0,) for call in sleep.await_args_list)


def test_run_sobrevive_a_un_fallo_de_red() -> None:
    engine, calculator = _engine(_payload()), _calculator()
    engine.fetch_nasdaq_quote.side_effect = OSError("red caída")
    scheduler = IngestionScheduler(engine, calculator, interval_s=0.0)  # type: ignore[arg-type]

    with patch("src.ingestion.scheduler.asyncio.sleep", new_callable=AsyncMock):
        asyncio.run(scheduler.run(max_iterations=2))  # no debe lanzar excepción

    assert engine.fetch_nasdaq_quote.await_count == 2
    engine.write_to_influx.assert_not_awaited()


def test_sorted_candles_ordena_por_timestamp_no_por_insercion() -> None:
    candles = _sorted_candles(_payload())
    assert [c.timestamp for c in candles] == sorted(c.timestamp for c in candles)
    assert candles[-1].price_close == 102.0


def test_variation_pct_calcula_bien_entre_cierres() -> None:
    assert _variation_pct(101.0, 102.0) == (((102.0 - 101.0) / 101.0) * 100)
