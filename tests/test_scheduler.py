"""Pruebas del orquestador — RF-101 (cadencia de 5 minutos) del walking skeleton.

Usan mocks (unittest.mock): sin InfluxDB, sin red y sin dependencias extra.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.ingestion.engine import parse_raw_candle
from src.ingestion.huecos import InformeHueco
from src.ingestion.scheduler import IngestionScheduler, _sorted_candles, _variation_pct

_LATEST_RAW = {
    "open": 101.0, "high": 102.5, "low": 100.5,
    "close": 102.0, "volume": 1000.0,
}
_PREVIOUS_RAW = {
    "open": 100.0, "high": 101.5, "low": 99.5,
    "close": 101.0, "volume": 900.0,
}


def _payload() -> dict[str, Any]:
    # Orden de inserción invertido a propósito: 09:35 aparece antes que 09:30
    return {"candles": {
        "2026-10-06 09:35:00": _LATEST_RAW,
        "2026-10-06 09:30:00": _PREVIOUS_RAW,
    }}


def _engine(payload: dict[str, Any], valid: bool = True) -> Mock:
    engine = Mock()
    engine.fetch_nasdaq_quote = AsyncMock(return_value=payload)
    engine.validate_payload = Mock(return_value=valid)
    engine.write_to_influx = AsyncMock()
    # Recuperación de huecos (B2.5): sin última vela persistida, la pasada no
    # pide nada y el tick se prueba igual que antes.
    engine.ultima_vela_ms = AsyncMock(return_value=None)
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
    # Firma OHLC de SDD §5.1.2 (v1.1, CR-001): ts, open, high, low, close, volume
    engine.write_to_influx.assert_awaited_once_with(
        expected_ts, 101.0, 102.5, 100.5, 102.0, 1000.0
    )
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

    with (
        patch("src.ingestion.scheduler.asyncio.sleep", new_callable=AsyncMock) as sleep,
        patch("src.ingestion.scheduler.ventana_de_consulta", return_value="sesion"),
    ):
        asyncio.run(scheduler.run(max_iterations=3))

    assert engine.fetch_nasdaq_quote.await_count == 3
    assert engine.write_to_influx.await_count == 3
    assert sleep.await_count == 3
    assert all(call.args == (300.0,) for call in sleep.await_args_list)


def test_run_sobrevive_a_un_fallo_de_red() -> None:
    engine, calculator = _engine(_payload()), _calculator()
    engine.fetch_nasdaq_quote.side_effect = OSError("red caída")
    scheduler = IngestionScheduler(engine, calculator, interval_s=0.0)  # type: ignore[arg-type]

    with (
        patch("src.ingestion.scheduler.asyncio.sleep", new_callable=AsyncMock),
        patch("src.ingestion.scheduler.ventana_de_consulta", return_value="sesion"),
    ):
        asyncio.run(scheduler.run(max_iterations=2))  # no debe lanzar excepción

    assert engine.fetch_nasdaq_quote.await_count == 2
    engine.write_to_influx.assert_not_awaited()


def test_fuera_de_sesion_no_consulta_la_fuente_y_recupera_una_vez() -> None:
    """P1.9: sin ventana de sesión no se gasta ni un crédito (~288/día antes)."""
    engine, calculator = _engine(_payload()), _calculator()
    scheduler = IngestionScheduler(engine, calculator, interval_s=0.0)  # type: ignore[arg-type]
    recuperacion = AsyncMock(return_value=InformeHueco("sin-hueco"))

    with (
        patch("src.ingestion.scheduler.asyncio.sleep", new_callable=AsyncMock),
        patch("src.ingestion.scheduler.ventana_de_consulta", return_value="ninguna"),
        patch("src.ingestion.scheduler.recuperar_hueco", recuperacion),
    ):
        asyncio.run(scheduler.run(max_iterations=4))

    assert recuperacion.await_count == 1  # solo el arranque
    engine.fetch_nasdaq_quote.assert_not_awaited()
    engine.write_to_influx.assert_not_awaited()


def test_tras_el_cierre_la_recuperacion_corre_una_sola_vez() -> None:
    """P1.9: tras el margen de cierre, UNA recuperación del día y sin fetch."""
    engine, calculator = _engine(_payload()), _calculator()
    scheduler = IngestionScheduler(engine, calculator, interval_s=0.0)  # type: ignore[arg-type]
    recuperacion = AsyncMock(return_value=InformeHueco("sin-hueco"))

    with (
        patch("src.ingestion.scheduler.asyncio.sleep", new_callable=AsyncMock),
        patch("src.ingestion.scheduler.ventana_de_consulta", return_value="cierre"),
        patch("src.ingestion.scheduler.recuperar_hueco", recuperacion),
    ):
        asyncio.run(scheduler.run(max_iterations=5))

    assert recuperacion.await_count == 1
    engine.fetch_nasdaq_quote.assert_not_awaited()


def test_sorted_candles_ordena_por_timestamp_no_por_insercion() -> None:
    candles = _sorted_candles(_payload())
    assert [c.timestamp for c in candles] == sorted(c.timestamp for c in candles)
    assert candles[-1].price_close == 102.0


def test_variation_pct_calcula_bien_entre_cierres() -> None:
    assert _variation_pct(101.0, 102.0) == (((102.0 - 101.0) / 101.0) * 100)


# ---------------------------------------------------------------------------
# main() — el arranque real del scheduler en M2
# ---------------------------------------------------------------------------


class _ConfiguracionesDePrueba:
    """Repositorio estricto, sin atributos inventados.

    Con un `Mock` el typo `seembrar_inicial` del Sprint 3 habría pasado
    inadvertido (un Mock fabrica cualquier atributo), y ese typo dejó la
    siembra de `system_configurations` sin ejecutar en el primer arranque
    real de un contenedor nuevo: solo un ERROR en el log.
    """

    def __init__(self) -> None:
        self.sembradas = 0

    def sembrar_inicial(self) -> bool:
        self.sembradas += 1
        return True


def test_main_siembra_la_configuracion_y_arranca_el_ciclo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.ingestion import scheduler as mod_sched

    monkeypatch.setenv("TWELVE_DATA_API_KEY_ENC", "cualquier-cifrado")
    monkeypatch.setenv("CREDENTIALS_MASTER_KEY", "00" * 32)
    monkeypatch.setenv("INFLUXDB_ADMIN_TOKEN", "token-falso")

    configuraciones = _ConfiguracionesDePrueba()
    scheduler_falso = Mock()
    scheduler_falso.return_value.run = AsyncMock()

    monkeypatch.setattr("src.security.crypto.decrypt_secret", lambda valor: "clave")
    monkeypatch.setattr("influxdb_client.InfluxDBClient", Mock(return_value=Mock()))
    monkeypatch.setattr(
        "src.database.configuraciones.ConfiguracionesPostgres",
        Mock(return_value=configuraciones),
    )
    monkeypatch.setattr("src.database.influx_lectura.LectorInflux", Mock())
    monkeypatch.setattr("src.database.predictions.PrediccionesPostgres", Mock())
    monkeypatch.setattr(mod_sched, "IngestionEngine", Mock())
    monkeypatch.setattr(mod_sched, "InferenceClient", Mock())
    monkeypatch.setattr(mod_sched, "IngestionScheduler", scheduler_falso)

    mod_sched.main()

    # La siembra es lo que garantiza la fila única de system_configurations
    # (D3): tiene que ejecutarse, no solo intentarse.
    assert configuraciones.sembradas == 1
    scheduler_falso.return_value.run.assert_awaited_once()
