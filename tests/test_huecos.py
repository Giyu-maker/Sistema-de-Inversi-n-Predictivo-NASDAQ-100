"""Pruebas de la recuperación de huecos de ingesta (B2.5) y su cuota.

Sin red y sin InfluxDB real: proveedor falso de tramos + mocks. Los casos
cubren lo que pide el plan de B2.5 endurecido para el lunes: hueco interno,
sesión cerrada con una sola vela (el caso real del 23/10), sin hueco, fuera
de sesión (sin candidatas) y límites de cuota (8/min, 800/día), más el
cableado en el scheduler.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from zoneinfo import ZoneInfo

from src.ingestion.engine import IngestionEngine
from src.ingestion.huecos import CuotaBasica, InformeHueco, recuperar_hueco
from src.ingestion.scheduler import IngestionScheduler

_NY = ZoneInfo("America/New_York")
_RAW = {"open": 100.0, "high": 101.5, "low": 99.5, "close": 101.0, "volume": 900.0}
_INTERVALO_MS = 5 * 60 * 1000


def _ny(año: int, mes: int, dia: int, hora: int, minuto: int = 0) -> datetime:
    return datetime(año, mes, dia, hora, minuto, tzinfo=_NY)


def _slots(dia: date, hasta_k: int | None = None) -> list[int]:
    """Los 78 inicios de vela de la sesión (o los primeros `hasta_k`)."""
    base = int(datetime.combine(dia, time(9, 30), tzinfo=_NY).timestamp() * 1000)
    cantidad = 78 if hasta_k is None else hasta_k
    return [base + k * _INTERVALO_MS for k in range(cantidad)]


class _ProveedorRango:
    """Fake de MarketDataProvider que registra las llamadas de tramo."""

    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload if payload is not None else {"candles": {}}
        self.llamadas: list[tuple[str, str]] = []

    async def fetch_candles(self) -> dict[str, Any]:
        return {"candles": {}}

    async def fetch_candles_range(
        self, start_date: str, end_date: str, outputsize: int = 5000
    ) -> dict[str, Any]:
        self.llamadas.append((start_date, end_date))
        return self.payload


def _engine_con(
    existentes: set[int], payload: dict[str, Any] | None = None
) -> tuple[IngestionEngine, _ProveedorRango]:
    """Engine falso: `existing_timestamps` responde con el conjunto dado."""
    proveedor = _ProveedorRango(payload)
    engine = IngestionEngine(MagicMock(), "clave", provider=proveedor)  # type: ignore[arg-type]

    async def _existentes(timestamps: Any) -> set[int]:
        return set(timestamps) & existentes

    engine.existing_timestamps = AsyncMock(side_effect=_existentes)  # type: ignore[method-assign]
    engine.ultima_vela_ms = AsyncMock(  # type: ignore[method-assign]
        return_value=max(existentes) if existentes else None
    )
    engine.write_candles_batch = AsyncMock(return_value=(1, 0))  # type: ignore[method-assign]
    return engine, proveedor


class TestDeteccionDeHueco:
    def test_hueco_interno_pide_desde_la_primera_vela_faltante(self) -> None:
        # Martes en curso a las 11:00 (lunes íntegro); velas 09:30–10:00 del
        # martes escritas: el tramo posterior está completo, pero 10:05–10:55 falta.
        dia = date(2026, 10, 20)
        existentes = set(_slots(date(2026, 10, 19))) | set(_slots(dia, hasta_k=7))
        payload = {"candles": {"2026-10-20 10:05:00": _RAW, "2026-10-20 10:10:00": _RAW}}
        engine, proveedor = _engine_con(existentes, payload)

        informe = asyncio.run(
            recuperar_hueco(engine, CuotaBasica(), ahora=_ny(2026, 10, 20, 11, 0))
        )

        assert informe.motivo == "recuperado"
        assert informe.pedidas == 1
        assert proveedor.llamadas == [("2026-10-20 10:05:00", "2026-10-20 11:00:00")]
        # Camino insert-if-absent de CR-003, no una escritura a ciegas.
        engine.write_candles_batch.assert_awaited_once()
        assert informe.escritas == 1

    def test_hueco_interno_con_ultima_vela_reciente_no_se_escapa(self) -> None:
        # La ÚLTIMA vela es reciente (10:55) pero faltan las intermedias:
        # la regla vieja "última vela + 1 intervalo" habría dicho sin-hueco.
        dia = date(2026, 10, 20)
        existentes = (
            set(_slots(date(2026, 10, 19)))
            | set(_slots(dia, hasta_k=7))
            | {_slots(dia)[25]}  # 10:55
        )
        payload = {"candles": {"2026-10-20 10:05:00": _RAW}}
        engine, proveedor = _engine_con(existentes, payload)

        informe = asyncio.run(
            recuperar_hueco(engine, CuotaBasica(), ahora=_ny(2026, 10, 20, 11, 0))
        )

        assert informe.motivo == "recuperado"
        assert proveedor.llamadas == [("2026-10-20 10:05:00", "2026-10-20 11:00:00")]

    def test_sesion_cerrada_con_una_sola_vela_se_recupera(self) -> None:
        # Domingo: la candidata es el viernes 23/10 con UNA vela de 78 (el
        # caso real del hallazgo). El tramo cierra en 15:55 + 5 min = 16:00.
        existentes = set(_slots(date(2026, 10, 23), hasta_k=1))
        payload = {"candles": {"2026-10-23 09:35:00": _RAW}}
        engine, proveedor = _engine_con(existentes, payload)

        informe = asyncio.run(
            recuperar_hueco(engine, CuotaBasica(), ahora=_ny(2026, 10, 25, 12, 0))
        )

        assert informe.motivo == "recuperado"
        assert informe.pedidas == 1
        assert proveedor.llamadas == [("2026-10-23 09:35:00", "2026-10-23 16:00:00")]

    def test_sesiones_completas_no_piden_nada(self) -> None:
        # Lunes íntegro + martes completo hasta el último slot transcurrido.
        existentes = set(_slots(date(2026, 10, 19))) | set(_slots(date(2026, 10, 20), hasta_k=24))
        engine, proveedor = _engine_con(existentes)

        informe = asyncio.run(
            recuperar_hueco(engine, CuotaBasica(), ahora=_ny(2026, 10, 20, 11, 0))
        )

        assert informe.motivo == "sin-hueco"
        assert proveedor.llamadas == []
        engine.write_candles_batch.assert_not_awaited()

    def test_antes_de_la_apertura_recupera_la_sesion_anterior(self) -> None:
        # Martes 08:00: hoy aún no empezó, pero el lunes cerró con hueco.
        faltantes = set(_slots(date(2026, 10, 19))) - set(_slots(date(2026, 10, 19), hasta_k=75))
        existentes = set(_slots(date(2026, 10, 19))) - faltantes
        payload = {"candles": {"2026-10-19 15:45:00": _RAW}}
        engine, proveedor = _engine_con(existentes, payload)

        informe = asyncio.run(
            recuperar_hueco(engine, CuotaBasica(), ahora=_ny(2026, 10, 20, 8, 0))
        )

        assert informe.motivo == "recuperado"
        assert proveedor.llamadas == [("2026-10-19 15:45:00", "2026-10-19 16:00:00")]

    def test_ayer_completo_y_hoy_sin_hueco_no_pide(self) -> None:
        # Antes de las 09:30: la sesión de hoy no tiene slots transcurridos.
        existentes = set(_slots(date(2026, 10, 19)))
        engine, proveedor = _engine_con(existentes)

        informe = asyncio.run(
            recuperar_hueco(engine, CuotaBasica(), ahora=_ny(2026, 10, 20, 8, 0))
        )

        assert informe.motivo == "sin-hueco"
        assert proveedor.llamadas == []

    def test_sin_datos_previos_es_trabajo_del_backfill(self) -> None:
        engine, proveedor = _engine_con(set())

        informe = asyncio.run(
            recuperar_hueco(engine, CuotaBasica(), ahora=_ny(2026, 10, 20, 11, 0))
        )

        assert informe.motivo == "sin-datos"
        assert proveedor.llamadas == []


class TestFueraDeSesion:
    def test_una_semana_de_feriados_no_tiene_candidatas(self) -> None:
        # Domingo 25/10 con los 5 días hábiles previos configurados como
        # feriado: no hay ninguna sesión candidata en la mirada hacia atrás.
        existentes = set(_slots(date(2026, 10, 16), hasta_k=1))
        feriados = frozenset(
            date(2026, 10, d) for d in (19, 20, 21, 22, 23)
        )
        engine, proveedor = _engine_con(existentes)

        informe = asyncio.run(
            recuperar_hueco(
                engine, CuotaBasica(),
                ahora=_ny(2026, 10, 25, 12, 0), feriados=feriados,
            )
        )

        assert informe.motivo == "fuera-de-sesion"
        assert proveedor.llamadas == []

    def test_feriado_de_ayer_con_la_anterior_completa_no_pide(self) -> None:
        # Acción de Gracias (jueves feriado): la candidata es el miércoles,
        # que está completa → sin-hueco, sin gastar créditos.
        existentes = set(_slots(date(2026, 11, 25)))
        feriados = frozenset({date(2026, 11, 26)})
        engine, proveedor = _engine_con(existentes)

        informe = asyncio.run(
            recuperar_hueco(
                engine, CuotaBasica(),
                ahora=_ny(2026, 11, 26, 12, 0), feriados=feriados,
            )
        )

        assert informe.motivo == "sin-hueco"
        assert proveedor.llamadas == []


class TestCuota:
    def test_ocho_por_minuto_bloquean_y_el_minuto_siguiente_libera(self) -> None:
        reloj = {"ms": _ny(2026, 10, 20, 11, 0).timestamp() * 1000}
        cuota = CuotaBasica(reloj=lambda: reloj["ms"])
        existentes = set(_slots(date(2026, 10, 19))) | set(_slots(date(2026, 10, 20), hasta_k=7))

        for _ in range(8):
            cuota.consumir()
        engine, proveedor = _engine_con(existentes)
        informe = asyncio.run(
            recuperar_hueco(engine, cuota, ahora=_ny(2026, 10, 20, 11, 0))
        )

        assert informe.motivo == "sin-cuota"
        assert proveedor.llamadas == []

        # Ventana deslizante: pasado un minuto vuelve a haber hueco.
        reloj["ms"] += 61_000
        assert cuota.puede_consumir()

    def test_ochocientos_al_dia_bloquean_aunque_quede_minuto(self) -> None:
        reloj = {"ms": _ny(2026, 10, 20, 15, 0).timestamp() * 1000}  # 15:00 ET = 19:00 UTC
        cuota = CuotaBasica(reloj=lambda: reloj["ms"])
        # 800 créditos consumidos hace una hora: mismo día UTC, fuera del minuto.
        consumos_previos = [reloj["ms"] - 3_600_000] * 800
        cuota._consumos.extend(consumos_previos)  # noqa: SLF001 — arreglo de test

        assert not cuota.puede_consumir()
        existentes = set(_slots(date(2026, 10, 19))) | set(_slots(date(2026, 10, 20), hasta_k=29))
        engine, proveedor = _engine_con(existentes)
        informe = asyncio.run(
            recuperar_hueco(engine, cuota, ahora=_ny(2026, 10, 20, 15, 0))
        )
        assert informe.motivo == "sin-cuota"
        assert proveedor.llamadas == []

        # Con 799 sigue cabiendo el crédito que falta.
        cuota._consumos.pop()  # noqa: SLF001
        assert cuota.puede_consumir()

    def test_los_consumos_de_ayer_no_cuentan_hoy(self) -> None:
        reloj = {"ms": _ny(2026, 10, 20, 11, 0).timestamp() * 1000}
        cuota = CuotaBasica(reloj=lambda: reloj["ms"])
        # Mediodía de ayer en Nueva York: también es ayer en UTC (un importe
        # de la madrugada NO serviría, sería el mismo día UTC de hoy).
        ayer = _ny(2026, 10, 19, 12, 0).timestamp() * 1000
        cuota._consumos.extend([ayer] * 800)  # noqa: SLF001

        assert cuota.puede_consumir()


class TestErroresDeProveedor:
    def test_error_del_proveedor_se_reporta_y_no_escribe(self) -> None:
        existentes = set(_slots(date(2026, 10, 19))) | set(_slots(date(2026, 10, 20), hasta_k=7))
        engine, proveedor = _engine_con(existentes, {"error": "429 Too Many Requests"})

        informe = asyncio.run(
            recuperar_hueco(engine, CuotaBasica(), ahora=_ny(2026, 10, 20, 11, 0))
        )

        assert informe.motivo == "payload-invalido"
        assert informe.pedidas == 1  # el crédito ya se gastó: consta en el informe
        engine.write_candles_batch.assert_not_awaited()

    def test_tramo_vacio_no_escribe(self) -> None:
        existentes = set(_slots(date(2026, 10, 19))) | set(_slots(date(2026, 10, 20), hasta_k=7))
        engine, _ = _engine_con(existentes, {"candles": {}})

        informe = asyncio.run(
            recuperar_hueco(engine, CuotaBasica(), ahora=_ny(2026, 10, 20, 11, 0))
        )

        assert informe.motivo == "sin-rango"
        engine.write_candles_batch.assert_not_awaited()

    def test_payload_mal_formado_no_escribe(self) -> None:
        existentes = set(_slots(date(2026, 10, 19))) | set(_slots(date(2026, 10, 20), hasta_k=7))
        malo = {"open": "no-numero", "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0}
        engine, _ = _engine_con(existentes, {"candles": {"2026-10-20 10:05:00": malo}})

        informe = asyncio.run(
            recuperar_hueco(engine, CuotaBasica(), ahora=_ny(2026, 10, 20, 11, 0))
        )

        assert informe.motivo == "payload-invalido"
        engine.write_candles_batch.assert_not_awaited()


class TestCableadoEnElScheduler:
    def test_run_once_recupera_huecos_antes_del_tick(self) -> None:
        recuperacion = AsyncMock(return_value=InformeHueco("sin-hueco"))
        engine = Mock()
        engine.fetch_nasdaq_quote = AsyncMock(return_value={"candles": {}})
        engine.validate_payload = Mock(return_value=False)  # corta tras el fetch
        calculator = Mock()
        scheduler = IngestionScheduler(engine, calculator)  # type: ignore[arg-type]

        with patch("src.ingestion.scheduler.recuperar_hueco", recuperacion):
            asyncio.run(scheduler.run_once())

        recuperacion.assert_awaited_once()

    def test_cada_tick_de_la_cadencia_recupera(self) -> None:
        recuperacion = AsyncMock(return_value=InformeHueco("sin-hueco"))
        engine = Mock()
        engine.fetch_nasdaq_quote = AsyncMock(return_value={"candles": {}})
        engine.validate_payload = Mock(return_value=False)
        engine.ultima_vela_ms = AsyncMock(return_value=None)
        calculator = Mock()
        scheduler = IngestionScheduler(engine, calculator, interval_s=0.0)  # type: ignore[arg-type]

        with (
            patch("src.ingestion.scheduler.recuperar_hueco", recuperacion),
            patch("src.ingestion.scheduler.asyncio.sleep", new_callable=AsyncMock),
        ):
            asyncio.run(scheduler.run(max_iterations=3))

        assert recuperacion.await_count == 3  # arranque + cada tick

    def test_un_fallo_de_recuperacion_no_detiene_el_tick(self) -> None:
        engine = Mock()
        engine.fetch_nasdaq_quote = AsyncMock(
            return_value={"candles": {"2026-10-20 11:00:00": _RAW}}
        )
        engine.validate_payload = Mock(return_value=True)
        engine.write_to_influx = AsyncMock()
        engine.ultima_vela_ms = AsyncMock(side_effect=RuntimeError("Influx caído"))
        calculator = Mock()
        scheduler = IngestionScheduler(engine, calculator)  # type: ignore[arg-type]

        asyncio.run(scheduler.run_once())  # no debe lanzar

        engine.fetch_nasdaq_quote.assert_awaited_once()
        engine.write_to_influx.assert_awaited_once()
