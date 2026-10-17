"""Backfill de histórico de velas de 5M desde Twelve Data a InfluxDB (Paso 1, Sprint 3).

No forma parte de RF-101 (que es el muestreo cíclico del Sprint 2): es la carga
inicial de datos sobre la que se construye el dataset del modelo predictivo
(RF-301). Usa la misma `MarketDataProvider` que el orquestador y escribe a
través de `IngestionEngine.write_candles_batch()`, de modo que **la guarda de
inmutabilidad de CR-003 queda activa también aquí** y re-ejecutar el backfill
es idempotente.

Límites del plan gratuito Basic de Twelve Data (documentación oficial):
- `outputsize` 1–5000 puntos por petición; `/time_series` cuesta 1 crédito.
- 8 créditos/min y 800/día → se aplica un *throttle* entre ventanas.
- El intradía está disponible desde 2022 para acciones/ETF de EE. UU.

Uso::

    .venv/bin/python -m src.ingestion.backfill                  # BACKFILL_DAYS (500 por defecto)
    .venv/bin/python -m src.ingestion.backfill --trading-days 120
    .venv/bin/python -m src.ingestion.backfill --trading-days 120 --dry-run

`BACKFILL_DAYS` cuenta **sesiones bursátiles**, no días de calendario:
500 sesiones ≈ 2 años ≈ 39.000 velas ≈ 8 peticiones de 5000.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from influxdb_client import InfluxDBClient

from src.ingestion.engine import IngestionEngine, NasdaqCandle, parse_raw_candle
from src.ingestion.providers.twelve_data import TwelveDataProvider

logger = logging.getLogger(__name__)

_NY = ZoneInfo("America/New_York")
_API_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_MAX_OUTPUTSIZE = 5000          # límite documentado por Twelve Data
_THROTTLE_S = 7.6               # 8 créditos/min → margen bajo el límite de 8 req/min
_SAFETY_MAX_REQUESTS = 200      # nunca más de 200 peticiones (25% de la cuota diaria)


@dataclass
class BackfillReport:
    """Resultado de un backfill, para reportarlo con honestidad (RF-301)."""

    peticiones: int = 0
    velas_unicas: int = 0
    velas_escritas: int = 0
    velas_omitidas: int = 0
    dias_bursatiles: int = 0
    primera_vela: int | None = None
    ultima_vela: int | None = None
    errores: list[str] = field(default_factory=list)

    @property
    def fecha_primera(self) -> str:
        if self.primera_vela is None:
            return "-"
        return datetime.fromtimestamp(self.primera_vela / 1000, tz=_NY).strftime(_API_DATE_FORMAT)

    @property
    def fecha_ultima(self) -> str:
        if self.ultima_vela is None:
            return "-"
        return datetime.fromtimestamp(self.ultima_vela / 1000, tz=_NY).strftime(_API_DATE_FORMAT)


def _ny_string(epoch_ms: int) -> str:
    """Epoch ms → ``YYYY-MM-DD HH:MM:SS`` en hora de Nueva York (formato de la API)."""
    return datetime.fromtimestamp(epoch_ms / 1000, tz=_NY).strftime(_API_DATE_FORMAT)


async def run_backfill(
    engine: IngestionEngine,
    provider: TwelveDataProvider,
    target_trading_days: int,
    *,
    end_ms: int,
    dry_run: bool = False,
    throttle_s: float = _THROTTLE_S,
) -> BackfillReport:
    """Recorre el histórico hacia atrás hasta cubrir `target_trading_days` sesiones.

    Paginado: cada petición devuelve hasta `outputsize` velas **dentro** de la
    ventana (las más recientes). Si se llena el máximo, la siguiente ventana se
    cierra justo por debajo de la vela más antigua recibida y se repite.
    """
    report = BackfillReport()
    wanted: dict[int, NasdaqCandle] = {}
    start_floor = end_ms - (target_trading_days * 4) * 86_400_000  # holgura de calendario
    window_end = _ny_string(end_ms)

    while len({c.timestamp // 86_400_000 for c in wanted.values()}) < target_trading_days:
        if report.peticiones >= _SAFETY_MAX_REQUESTS:
            report.errores.append(
                f"Límite de seguridad de {_SAFETY_MAX_REQUESTS} peticiones alcanzado."
            )
            break

        payload = await provider.fetch_candles_range(
            start_date=_ny_string(start_floor), end_date=window_end,
            outputsize=_MAX_OUTPUTSIZE,
        )
        report.peticiones += 1

        if "error" in payload:
            report.errores.append(str(payload["error"]))
            logger.error("Twelve Data rechazó la ventana que termina en %s: %s",
                         window_end, payload["error"])
            break

        series = payload.get("candles")
        if not isinstance(series, dict) or not series:
            logger.info("Ventana vacía hasta %s: se agota el histórico disponible.", window_end)
            break

        ventana = [
            parse_raw_candle(ts, raw) for ts, raw in series.items()
        ]
        before = len(wanted)
        wanted.update({c.timestamp: c for c in ventana})
        nuevos = len(wanted) - before
        oldest = min(c.timestamp for c in ventana)
        logger.info(
            "Ventana %s → %s: %d velas (%d nuevas, total %d)",
            _ny_string(oldest), window_end, len(ventana), nuevos, len(wanted),
        )

        if len(ventana) < _MAX_OUTPUTSIZE:
            # La ventana no se llenó: no hay más datos antes del piso.
            break
        if oldest <= start_floor:
            break
        # Siguiente ventana terminando justo ANTES de la vela más antigua (end es
        # inclusivo, así que restamos 1 ms para no repetir el borde).
        window_end = _ny_string(oldest - 1)
        if throttle_s:
            await asyncio.sleep(throttle_s)

    ordered = sorted(wanted.values(), key=lambda c: c.timestamp)
    report.velas_unicas = len(ordered)
    if ordered:
        report.primera_vela = ordered[0].timestamp
        report.ultima_vela = ordered[-1].timestamp
        report.dias_bursatiles = len({c.timestamp // 86_400_000 for c in ordered})

    if dry_run:
        logger.info("DRY-RUN: no se escribe nada en InfluxDB.")
        return report

    if ordered:
        escritas, omitidas = await engine.write_candles_batch(ordered)
        report.velas_escritas = escritas
        report.velas_omitidas = omitidas
    return report


def _print_report(report: BackfillReport, elapsed_s: float) -> None:
    print()
    print("=" * 68)
    print("REPORTE DE BACKFILL — histórico de velas de 5M (QQQ / Twelve Data Basic)")
    print("=" * 68)
    print(f"  Peticiones a la API      : {report.peticiones} "
          f"(throttle {_THROTTLE_S} s, límite 8/min, 800/día)")
    print(f"  Velas únicas obtenidas   : {report.velas_unicas}")
    print(f"  Escritas en InfluxDB     : {report.velas_escritas}")
    print(f"  Omitidas por CR-003      : {report.velas_omitidas} (ya existentes)")
    print(f"  Días bursátiles cubiertos: {report.dias_bursatiles}")
    print(f"  Primera vela             : {report.fecha_primera}")
    print(f"  Última vela              : {report.fecha_ultima}")
    if report.dias_bursatiles:
        print(f"  Muestras por sesión (≈)  : {report.velas_unicas / report.dias_bursatiles:.1f}")
    print(f"  Duración                 : {elapsed_s:.1f} s")
    if report.errores:
        print("  ERRORES:")
        for error in report.errores:
            print(f"    - {error}")
    print("=" * 68)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill de velas de 5M desde Twelve Data a InfluxDB (RF-301).",
    )
    parser.add_argument(
        "--trading-days", type=int,
        default=int(os.environ.get("BACKFILL_DAYS", "500")),
        help="Sesiones bursátiles objetivo (env BACKFILL_DAYS; por defecto 500 ≈ 2 años).",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Consulta la API pero no escribe en InfluxDB.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    from src.security.crypto import decrypt_secret

    api_key = decrypt_secret(os.environ["TWELVE_DATA_API_KEY_ENC"])
    influx = InfluxDBClient(
        url=os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086"),
        token=os.environ["INFLUXDB_ADMIN_TOKEN"],
        org=os.environ.get("INFLUXDB_ORG", "ritech"),
    )
    engine = IngestionEngine(influx, api_key)
    provider = TwelveDataProvider(api_key=api_key, symbol=IngestionEngine.SYMBOL)

    started = time.perf_counter()
    report = asyncio.run(run_backfill(
        engine, provider, args.trading_days,
        end_ms=int(time.time() * 1000), dry_run=args.dry_run,
    ))
    _print_report(report, time.perf_counter() - started)
    influx.close()


if __name__ == "__main__":
    main()
