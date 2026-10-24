"""Pruebas de la recuperación de huecos de ingesta (B2.5) y su cuota.

Sin red y sin InfluxDB real: proveedor falso de tramos + mocks. Los casos
cubren lo que pide el plan de B2.5: hueco, sin hueco, fuera de sesión y
límites de cuota (8/min, 800/día), más el cableado en el scheduler.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from zoneinfo import ZoneInfo

from src.ingestion.engine import IngestionEngine
from src.ingestion.huecos import CuotaBasica, InformeHueco, recuperar_hueco
from src.ingestion.scheduler import IngestionScheduler

_NY = ZoneInfo("America/New_York")
_RAW = {"open": 100.0, "high": 101.5, "low": 99.5, "close": 101.0, "volume": 900.0}


def _ny(año: int, mes: int, dia: int, hora: int, minuto: int = 0) -> datetime:
    return datetime(año, mes, dia, hora, minuto, tzinfo=_NY)


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


def _engine_con_ultima(ultima_ms: int | None, payload: dict[str, Any] | None = None) -> tuple[
    IngestionEngine, _ProveedorRango
]:
    proveedor = _ProveedorRango(payload)
    engine = IngestionEngine(MagicMock(), "clave", provider=proveedor)  # type: ignore[arg-type]
    engine.ultima_vela_ms = AsyncMock(return_value=ultima_ms)  # type: ignore[method-assign]
    engine.write_candles_batch = AsyncMock(return_value=(1, 0))  # type: ignore[method-assign]
    return engine, proveedor


class TestDeteccionDeHueco:
    def test_hueco_pide_el_tramo_faltante_y_lo_escribe(self) -> None:
        # Martes en sesión: última vela 10:00, ahora 11:00 → 60 min de hueco.
        ultima = int(_ny(2026, 10, 20, 10, 0).timestamp() * 1000)
        payload = {"candles": {"2026-10-20 10:05:00": _RAW, "2026-10-20 10:10:00": _RAW}}
        engine, proveedor = _engine_con_ultima(ultima, payload)

        informe = asyncio.run(
            recuperar_hueco(engine, CuotaBasica(), ahora=_ny(2026, 10, 20, 11, 0))
        )

        assert informe.motivo == "recuperado"
        assert informe.pedidas == 1
        assert proveedor.llamadas == [("2026-10-20 10:05:00", "2026-10-20 11:00:00")]
        # Camino insert-if-absent de CR-003, no una escritura a ciegas.
        engine.write_candles_batch.assert_awaited_once()
        assert informe.escritas == 1

    def test_sin_hueco_no_pide_nada(self) -> None:
        # Última vela a 3 minutos: dentro del intervalo de 5 min.
        ultima = int(_ny(2026, 10, 20, 10, 57).timestamp() * 1000)
        engine, proveedor = _engine_con_ultima(ultima)

        informe = asyncio.run(
            recuperar_hueco(engine, CuotaBasica(), ahora=_ny(2026, 10, 20, 11, 0))
        )

        assert informe.motivo == "sin-hueco"
        assert proveedor.llamadas == []
        engine.write_candles_batch.assert_not_awaited()

    def test_exactamente_un_intervalo_no_es_hueco(self) -> None:
        # La regla es "A MÁS de un intervalo": 5 min exactos → sin hueco.
        ultima = int(_ny(2026, 10, 20, 10, 55).timestamp() * 1000)
        engine, proveedor = _engine_con_ultima(ultima)

        informe = asyncio.run(
            recuperar_hueco(engine, CuotaBasica(), ahora=_ny(2026, 10, 20, 11, 0))
        )

        assert informe.motivo == "sin-hueco"
        assert proveedor.llamadas == []

    def test_hueco_acotado_a_la_apertura_de_hoy(self) -> None:
        # Última vela ayer al cierre: el tramo empieza HOY a las 09:30, no ayer.
        ultima = int(_ny(2026, 10, 19, 15, 55).timestamp() * 1000)
        payload = {"candles": {"2026-10-20 09:30:00": _RAW, "2026-10-20 09:35:00": _RAW}}
        engine, proveedor = _engine_con_ultima(ultima, payload)

        informe = asyncio.run(
            recuperar_hueco(engine, CuotaBasica(), ahora=_ny(2026, 10, 20, 10, 0))
        )

        assert informe.motivo == "recuperado"
        assert proveedor.llamadas == [("2026-10-20 09:30:00", "2026-10-20 10:00:00")]

    def test_sin_datos_previos_es_trabajo_del_backfill(self) -> None:
        engine, proveedor = _engine_con_ultima(None)

        informe = asyncio.run(
            recuperar_hueco(engine, CuotaBasica(), ahora=_ny(2026, 10, 20, 11, 0))
        )

        assert informe.motivo == "sin-datos"
        assert proveedor.llamadas == []


class TestFueraDeSesion:
    def test_fin_de_semana_no_pide_nada(self) -> None:
        engine, proveedor = _engine_con_ultima(int(_ny(2026, 10, 23, 15, 55).timestamp() * 1000))

        informe = asyncio.run(
            recuperar_hueco(engine, CuotaBasica(), ahora=_ny(2026, 10, 24, 12, 0))
        )

        assert informe.motivo == "fuera-de-sesion"
        assert proveedor.llamadas == []

    def test_antes_de_la_apertura_no_pide_nada(self) -> None:
        engine, proveedor = _engine_con_ultima(int(_ny(2026, 10, 19, 15, 55).timestamp() * 1000))

        informe = asyncio.run(
            recuperar_hueco(engine, CuotaBasica(), ahora=_ny(2026, 10, 20, 8, 0))
        )

        assert informe.motivo == "fuera-de-sesion"
        assert proveedor.llamadas == []

    def test_despues_del_cierre_no_pide_nada(self) -> None:
        engine, proveedor = _engine_con_ultima(int(_ny(2026, 10, 20, 15, 55).timestamp() * 1000))

        informe = asyncio.run(
            recuperar_hueco(engine, CuotaBasica(), ahora=_ny(2026, 10, 20, 17, 0))
        )

        assert informe.motivo == "fuera-de-sesion"
        assert proveedor.llamadas == []

    def test_feriado_configurado_no_pide_nada(self) -> None:
        engine, proveedor = _engine_con_ultima(int(_ny(2026, 11, 25, 15, 55).timestamp() * 1000))
        feriados = frozenset({datetime(2026, 11, 26).date()})  # Acción de Gracias

        informe = asyncio.run(
            recuperar_hueco(
                engine, CuotaBasica(),
                ahora=_ny(2026, 11, 26, 12, 0), feriados=feriados,
            )
        )

        assert informe.motivo == "fuera-de-sesion"
        assert proveedor.llamadas == []


class TestCuota:
    def test_ocho_por_minuto_bloquean_y_el_minuto_siguiente_libera(self) -> None:
        reloj = {"ms": _ny(2026, 10, 20, 11, 0).timestamp() * 1000}
        cuota = CuotaBasica(reloj=lambda: reloj["ms"])
        ultima = int(_ny(2026, 10, 20, 10, 0).timestamp() * 1000)

        for _ in range(8):
            cuota.consumir()
        engine, proveedor = _engine_con_ultima(ultima)
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
        ultima = int(_ny(2026, 10, 20, 14, 0).timestamp() * 1000)
        engine, proveedor = _engine_con_ultima(ultima)
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
        ultima = int(_ny(2026, 10, 20, 10, 0).timestamp() * 1000)
        engine, proveedor = _engine_con_ultima(ultima, {"error": "429 Too Many Requests"})

        informe = asyncio.run(
            recuperar_hueco(engine, CuotaBasica(), ahora=_ny(2026, 10, 20, 11, 0))
        )

        assert informe.motivo == "payload-invalido"
        assert informe.pedidas == 1  # el crédito ya se gastó: consta en el informe
        engine.write_candles_batch.assert_not_awaited()

    def test_tramo_vacio_no_escribe(self) -> None:
        ultima = int(_ny(2026, 10, 20, 10, 0).timestamp() * 1000)
        engine, _ = _engine_con_ultima(ultima, {"candles": {}})

        informe = asyncio.run(
            recuperar_hueco(engine, CuotaBasica(), ahora=_ny(2026, 10, 20, 11, 0))
        )

        assert informe.motivo == "sin-rango"
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
