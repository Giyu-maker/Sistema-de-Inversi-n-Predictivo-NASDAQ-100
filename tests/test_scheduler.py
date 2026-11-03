"""Pruebas del orquestador — RF-101 (cadencia de 5 minutos) del walking skeleton.

Usan mocks (unittest.mock): sin InfluxDB, sin red y sin dependencias extra.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.calculator.asymmetric import AsymmetricCalculator
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
    # El umbral NO se mockea: es la regla de RF-202 que se está probando, y
    # un Mock habría devuelto «siempre cierto» y encendido el Kill-Switch
    # en todos los tests.
    calculator.is_threshold_breached = AsymmetricCalculator.is_threshold_breached
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
    # El motor del Kill-Switch no es el objeto de esta prueba: montarlo aquí
    # intentaría una conexión real a PostgreSQL en medio de un test unitario.
    monkeypatch.setattr(mod_sched, "motor_desde_entorno", Mock(return_value=Mock()))
    monkeypatch.setattr(mod_sched, "IngestionScheduler", scheduler_falso)

    mod_sched.main()

    # La siembra es lo que garantiza la fila única de system_configurations
    # (D3): tiene que ejecutarse, no solo intentarse.
    assert configuraciones.sembradas == 1
    scheduler_falso.return_value.run.assert_awaited_once()


# ---------------------------------------------------------------------------
# Disparo automático del −1,25 % de la sesión (RF-202, RF-503)
# ---------------------------------------------------------------------------


def _sesion_cayendo(variacion_pct: float, n: int = 12):
    """Sesión cuya apertura es 500 y cuyo último cierre cae `variacion_pct`.

    Velas de `fakes.vela` (open = close = precio), así que la apertura de la
    sesión es el primer precio y el cierre el último.
    """
    from tests.fakes import APERTURA_MS, INTERVALO_MS, vela

    objetivo = 500.0 * (1 + variacion_pct / 100.0)
    return [
        vela(
            APERTURA_MS + i * INTERVALO_MS,
            500.0 + (objetivo - 500.0) * i / (n - 1),
        )
        for i in range(n)
    ]


def _payload_de(ultima) -> dict[str, Any]:
    """Payload del proveedor con el OHLC real de la última vela de la serie."""
    return {"candles": {"2026-10-06 09:35:00": {
        "open": ultima.price_open, "high": ultima.price_high,
        "low": ultima.price_low, "close": ultima.price_close,
        "volume": ultima.volume,
    }}}


def _motor_con(fila=None):
    """Motor REAL con repositorio, broker y auditoría en memoria."""
    from src.security.kill_switch import BanderaBloqueo, KillSwitchEngine
    from tests.fakes import (
        AuditorKillSwitchFalso,
        BrokerKillSwitchFalso,
        RepoKillSwitchFalso,
        estado_kill_switch,
    )

    repo = RepoKillSwitchFalso()
    if fila is not None:
        repo.fila = fila
    banda = BanderaBloqueo()
    broker = BrokerKillSwitchFalso()
    broker.banda = banda
    auditor = AuditorKillSwitchFalso()
    motor = KillSwitchEngine(
        repo, broker, auditor, banda=banda, ahora_ms=lambda: 1
    )
    return motor, repo, broker, auditor


def _scheduler_con(velas, motor, calculator=None):
    """Ciclo completo con dobles: lector con la serie, IA y PostgreSQL falsos."""
    from src.ingestion.engine import IngestionEngine
    from tests.fakes import ClienteInferenciaFalso, LectorFalso, PrediccionesFalso

    return IngestionScheduler(
        _engine(_payload_de(velas[-1])),  # type: ignore[arg-type]
        calculator or _calculator(),
        lector=LectorFalso(velas),
        cliente=ClienteInferenciaFalso(),
        predicciones=PrediccionesFalso(),
        kill_switch=motor,
    )


def test_run_once_evalua_el_umbral_de_perdida() -> None:
    # El hook está cableado en el ciclo, después de persistir la vela.
    from tests.fakes import LectorFalso

    motor, *_ = _motor_con()
    velas = _sesion_cayendo(-1.4)
    scheduler = _scheduler_con(velas, motor)

    with patch.object(scheduler, "_evaluar_umbral_de_perdida") as umbral:
        asyncio.run(scheduler.run_once())

    # La vela que recibe es la que acaba de persistir, la del payload (no la
    # última de la serie del lector, que es la de la sesión entera).
    umbral.assert_called_once()
    assert umbral.call_args.args[0].price_close == velas[-1].price_close
    assert isinstance(scheduler.lector, LectorFalso)


def test_dispara_automaticamente_al_cruzar_el_limite() -> None:
    from src.utils.mercado import ahora_ny

    motor, repo, broker, auditor = _motor_con()
    scheduler = _scheduler_con(_sesion_cayendo(-1.4), motor)

    asyncio.run(scheduler.run_once())

    assert repo.fila.activo is True, "la pérdida superó el 1,25 %"
    assert repo.fila.origen == "AUTOMATICO"
    assert repo.fila.actor == "SISTEMA"
    assert broker.liquidaciones == 1
    # Y la guarda anti-bucle ya no deja volver a disparar hoy.
    assert motor.puede_disparar_automatico(ahora_ny().date()) is False

    evento = auditor.eventos[-1]
    assert evento.actor == "SISTEMA"
    assert evento.ip_address == "127.0.0.1"
    assert evento.payload is not None
    assert evento.payload["origen"] == "AUTOMATICO"
    assert evento.payload["variacion_sesion_pct"] == pytest.approx(-1.4, abs=0.01)
    assert evento.payload["limite_pct"] == 1.25


def test_por_debajo_del_limite_no_dispara() -> None:
    motor, repo, broker, auditor = _motor_con()
    scheduler = _scheduler_con(_sesion_cayendo(-1.0), motor)  # −1,00 % > −1,25 %

    asyncio.run(scheduler.run_once())

    assert repo.fila.activo is False
    assert broker.liquidaciones == 0
    assert auditor.eventos == []


def test_en_el_limite_exacto_si_dispara() -> None:
    # `is_threshold_breached` es estricto hacia abajo: −1,25 % exacto cuenta.
    motor, repo, *_ = _motor_con()
    scheduler = _scheduler_con(_sesion_cayendo(-1.25), motor)

    asyncio.run(scheduler.run_once())

    assert repo.fila.activo is True


def test_no_vuelve_a_disparar_si_ya_esta_activo() -> None:
    from tests.fakes import estado_kill_switch

    motor, repo, broker, *_ = _motor_con(estado_kill_switch(activo=True, motivo="ya"))
    scheduler = _scheduler_con(_sesion_cayendo(-3.0), motor)

    asyncio.run(scheduler.run_once())

    assert broker.liquidaciones == 0, "no se liquidó dos veces"
    assert repo.activaciones == 0


def test_tras_un_reinicio_no_dispara_en_la_misma_sesion() -> None:
    from datetime import timedelta

    from src.utils.mercado import ahora_ny
    from tests.fakes import estado_kill_switch

    hoy = ahora_ny().date()
    motor, repo, broker, *_ = _motor_con(
        estado_kill_switch(sesion_reinicio=hoy, reinicio_en=1)
    )
    scheduler = _scheduler_con(_sesion_cayendo(-1.4), motor)

    asyncio.run(scheduler.run_once())

    assert repo.fila.activo is False, "hoy ya hubo reinicio: sin nuevo disparo"
    assert broker.liquidaciones == 0
    # Al día siguiente, la guarda deja volver a evaluar.
    assert motor.puede_disparar_automatico(hoy + timedelta(days=1)) is True


def test_un_gap_registra_la_perdida_real_en_el_payload() -> None:
    motor, repo, _broker, auditor = _motor_con()
    # El detector de gaps y su aviso, REALES: es la regla de RF-203 la que
    # se comprueba, no un texto de mentira.
    calculadora = _calculator()
    calculadora.is_market_gap = AsymmetricCalculator.is_market_gap
    calculadora.gap_loss_notice = AsymmetricCalculator.gap_loss_notice
    scheduler = _scheduler_con(_sesion_cayendo(-3.0), motor, calculator=calculadora)

    asyncio.run(scheduler.run_once())

    assert repo.fila.activo is True
    assert repo.fila.motivo.startswith("Pérdida de sesión")
    payload = auditor.eventos[-1].payload
    assert payload is not None
    assert payload["gap"] is True
    assert payload["perdida_real_pct"] == pytest.approx(-3.0, abs=0.01)
    assert payload["diferencia_pp"] == pytest.approx(1.75, abs=0.01)
    assert "GAP DE MERCADO" in payload["aviso_gap"]
    assert "RF-203" in payload["aviso_gap"]


def test_sin_lector_o_sin_motor_no_tumba_el_ciclo() -> None:
    motor, *_ = _motor_con()
    # Sin lector no hay sesión que evaluar: el hook sale sin tocar nada.
    sin_lector = IngestionScheduler(
        _engine(_payload()), _calculator(), kill_switch=motor  # type: ignore[arg-type]
    )
    asyncio.run(sin_lector.run_once())  # no debe lanzar

    # Sin motor no hay dónde disparar: el ciclo de datos sigue igual.
    from tests.fakes import LectorFalso

    sin_motor = IngestionScheduler(
        _engine(_payload()),  # type: ignore[arg-type]
        _calculator(),
        lector=LectorFalso(_sesion_cayendo(-3.0)),
        cliente=Mock(), predicciones=Mock(),
    )
    asyncio.run(sin_motor.run_once())  # idem
