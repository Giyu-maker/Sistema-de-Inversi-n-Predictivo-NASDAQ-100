"""Pruebas del backfill (Paso 1, Sprint 3) — paginado, idempotencia y errores.

Sin red y sin InfluxDB real: proveedor falso que replica la semántica documentada
de Twelve Data (`start_date`/`end_date` inclusivos, `outputsize` tomando las más
recientes) y cliente InfluxDB simulado.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from src.ingestion import backfill as backfill_mod
from src.ingestion.backfill import run_backfill
from src.ingestion.engine import NasdaqCandle

_NY = ZoneInfo("America/New_York")
_FMT = "%Y-%m-%d %H:%M:%S"

_RAW = {"open": 100.0, "high": 101.5, "low": 99.5, "close": 101.0, "volume": 900.0}


def _gen_sesiones(n_dias: int, inicio: tuple[int, int, int] = (2026, 4, 15)) -> dict[int, dict[str, Any]]:
    """Velas cada 5 min de 09:30 a 15:55 ET para `n_dias` sesiones contiguas (L–V)."""
    velas: dict[int, dict[str, Any]] = {}
    day = date(*inicio)
    while len({k // 86_400_000 for k in velas}) < n_dias:
        if day.weekday() < 5:  # lunes a viernes
            t = datetime(day.year, day.month, day.day, 9, 30, tzinfo=_NY)
            fin = datetime(day.year, day.month, day.day, 15, 55, tzinfo=_NY)
            while t <= fin:
                velas[int(t.timestamp() * 1000)] = dict(_RAW)
                t += timedelta(minutes=5)
        day = date.fromordinal(day.toordinal() + 1)
    return velas


class _FakeTwelveData:
    """Replica `fetch_candles_range` de Twelve Data: inclusivo y más recientes primero."""

    def __init__(self, velas: dict[int, dict[str, Any]], error: str | None = None) -> None:
        self.velas = velas
        self.error = error
        self.llamadas: list[dict[str, str]] = []

    async def fetch_candles_range(self, start_date: str, end_date: str,
                                  outputsize: int = 5000) -> dict[str, Any]:
        self.llamadas.append({"start_date": start_date, "end_date": end_date})
        if self.error is not None:
            return {"error": self.error}
        lo = int(datetime.strptime(start_date, _FMT).replace(tzinfo=_NY).timestamp() * 1000)
        hi = int(datetime.strptime(end_date, _FMT).replace(tzinfo=_NY).timestamp() * 1000)
        dentro = [ts for ts in self.velas if lo <= ts <= hi]
        recientes = sorted(dentro, reverse=True)[:outputsize]
        return {"candles": {self._clave(ts): self.velas[ts] for ts in recientes}}

    @staticmethod
    def _clave(ts: int) -> str:
        return datetime.fromtimestamp(ts / 1000, tz=_NY).strftime(_FMT)


class _FakeEngine:
    """Captura la llamada a lote en lugar de tocar InfluxDB."""

    def __init__(self) -> None:
        self.lotes: list[list[NasdaqCandle]] = []

    async def write_candles_batch(self, candles: list[NasdaqCandle]) -> tuple[int, int]:
        self.lotes.append(list(candles))
        return len(candles), 0


def _ejecutar(proveedor: Any, *, dias: int, end_ms: int, dry_run: bool = False,
              engine: Any = None) -> Any:
    return asyncio.run(run_backfill(
        engine or _FakeEngine(), proveedor, dias,
        end_ms=end_ms, dry_run=dry_run, throttle_s=0.0,
    ))


def _fin(n_dias: int = 1) -> int:
    """Instante posterior a la última vela de `n_dias` sesiones generadas."""
    return max(_gen_sesiones(n_dias)) + 3_600_000


class TestPaginado:
    def test_pagina_hacia_atras_hasta_cubrir_las_sesiones(self, monkeypatch) -> None:
        # `outputsize` pequeño fuerza varias ventanas; cada ventana debe mover el
        # `end_date` hacia atrás sin repetir el borde.
        monkeypatch.setattr(backfill_mod, "_MAX_OUTPUTSIZE", 100)
        velas = _gen_sesiones(6)          # 6 sesiones × 78 velas = 468 velas
        proveedor = _FakeTwelveData(velas)
        report = _ejecutar(proveedor, dias=6, end_ms=_fin(6))

        assert report.peticiones > 1, "debe paginar, no traer todo de una"
        assert report.dias_bursatiles == 6
        assert report.velas_unicas >= 400, "debe acumular varias sesiones (≥ 4 ventanas)"
        assert not report.errores
        # El ciclo se detiene al cumplir el objetivo, aunque la sesión más
        # antigua quede parcial: `dataset.py` descarta las sesiones incompletas,
        # así que no produce muestras inválidas (solo se pierde esa sesión).
        assert report.velas_unicas < len(velas)

        # Ninguna ventana debe repetir la vela de borde de la anterior.
        rangos = [(c["start_date"], c["end_date"]) for c in proveedor.llamadas]
        assert len(set(rangos)) == len(rangos)

    def test_ventana_no_llenada_detiene_el_paginado(self, monkeypatch) -> None:
        # Si una ventana devuelve menos del máximo, no hay más historia: parar
        # aunque aún falten sesiones (no hay datos disponibles).
        monkeypatch.setattr(backfill_mod, "_MAX_OUTPUTSIZE", 5000)
        velas = _gen_sesiones(2)
        proveedor = _FakeTwelveData(velas)
        report = _ejecutar(proveedor, dias=50, end_ms=_fin(2))

        assert report.peticiones == 1
        assert report.dias_bursatiles == 2

    def test_no_pide_ventanas_en_bucle_si_no_avanza(self, monkeypatch) -> None:
        monkeypatch.setattr(backfill_mod, "_MAX_OUTPUTSIZE", 5000)
        proveedor = _FakeTwelveData(_gen_sesiones(3))
        report = _ejecutar(proveedor, dias=500, end_ms=_fin(3))
        assert report.peticiones == 1  # ventana corta → corta de inmediato

    def test_limitede_seguridad_de_peticiones(self, monkeypatch) -> None:
        monkeypatch.setattr(backfill_mod, "_MAX_OUTPUTSIZE", 1)
        monkeypatch.setattr(backfill_mod, "_SAFETY_MAX_REQUESTS", 5)
        report = _ejecutar(_FakeTwelveData(_gen_sesiones(10)), dias=500, end_ms=_fin(10))
        assert report.peticiones == 5
        assert any("Límite de seguridad" in e for e in report.errores)


class TestErrores:
    def test_error_de_la_api_se_registra_y_no_lanza(self) -> None:
        report = _ejecutar(_FakeTwelveData({}, error="cuota agotada"), dias=10, end_ms=_fin(1))
        assert report.errores == ["cuota agotada"]
        assert report.velas_unicas == 0

    def test_respuesta_vacia_se_toma_como_fin_del_historico(self) -> None:
        report = _ejecutar(_FakeTwelveData({}), dias=10, end_ms=_fin(1))
        assert not report.errores
        assert report.velas_unicas == 0


class TestEscrituraEIdempotencia:
    def test_escribe_todo_en_un_unico_lote(self, monkeypatch) -> None:
        monkeypatch.setattr(backfill_mod, "_MAX_OUTPUTSIZE", 5000)
        engine = _FakeEngine()
        velas = _gen_sesiones(3)
        report = _ejecutar(_FakeTwelveData(velas), dias=3, end_ms=_fin(3), engine=engine)

        assert len(engine.lotes) == 1, "una sola escritura por lote, no una por vela"
        assert report.velas_escritas == len(velas)
        assert report.velas_omitidas == 0

    def test_dry_run_no_escribe(self) -> None:
        engine = _FakeEngine()
        report = _ejecutar(_FakeTwelveData(_gen_sesiones(2)), dias=2,
                           end_ms=_fin(2), dry_run=True, engine=engine)
        assert engine.lotes == []
        assert report.velas_escritas == 0
        assert report.velas_unicas > 0, "el dry-run sí debe contar lo que habría"

    def test_omite_las_velas_que_ya_estan(self, monkeypatch) -> None:
        monkeypatch.setattr(backfill_mod, "_MAX_OUTPUTSIZE", 5000)

        class _ConGuarda:
            def __init__(self) -> None:
                self.lote: list[NasdaqCandle] = []

            async def write_candles_batch(self, candles: list[NasdaqCandle]) -> tuple[int, int]:
                self.lote = list(candles)
                # Simula que la mitad ya existían (guarda CR-003 activa).
                escritas = (len(candles) + 1) // 2
                return escritas, len(candles) - escritas

        engine = _ConGuarda()
        report = _ejecutar(_FakeTwelveData(_gen_sesiones(2)), dias=2,
                           end_ms=_fin(2), engine=engine)
        assert report.velas_escritas + report.velas_omitidas == len(engine.lote)
        assert report.velas_omitidas > 0


class TestReporte:
    def test_fechas_en_hora_de_nueva_york(self) -> None:
        report = _ejecutar(_FakeTwelveData(_gen_sesiones(2)), dias=2, end_ms=_fin(2))
        assert report.fecha_primera == "2026-04-15 09:30:00"
        assert report.fecha_ultima != "-"

    def test_reporte_vacio_no_explota(self) -> None:
        report = _ejecutar(_FakeTwelveData({}), dias=5, end_ms=_fin(1))
        assert report.fecha_primera == "-"
        assert report.fecha_ultima == "-"


def test_el_backfill_corre_sin_torch() -> None:
    """Ajuste de diseño: M2 con solo el grupo `base` debe poder ejecutar el backfill.

    Se corre en un subproceso donde `torch` bloquea su propia importación.
    """
    import subprocess
    import sys

    from pathlib import Path

    raiz = Path(backfill_mod.__file__).resolve().parents[2]
    bloqueo = (
        "import sys\n"
        "class Bloqueo:\n"
        "    def find_module(self, name, path=None):\n"
        "        return self if name == 'torch' or name.startswith('torch.') else None\n"
        "    def load_module(self, name):\n"
        "        raise ImportError('torch no debe importarse en el backfill')\n"
        "sys.meta_path.insert(0, Bloqueo())\n"
        "import src.ingestion.backfill\n"
        "print('OK sin torch')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", bloqueo], capture_output=True, text=True,
        cwd=str(raiz), env={"PYTHONPATH": str(raiz), "PATH": "/usr/bin:/bin"},
        timeout=120,
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr}"
    assert "OK sin torch" in proc.stdout
