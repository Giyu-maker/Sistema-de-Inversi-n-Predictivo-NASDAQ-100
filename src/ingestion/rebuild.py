"""Migración única del bucket de InfluxDB a los 5 campos de CR-001 (Sprint 3).

**No es parte del pipeline.** Este módulo no lo invoca el orquestador, el
backfill ni ningún proceso automático: es una herramienta manual de migración
previa a datos de producción.

Por qué hace falta
------------------
CR-001 (aprobada 17/10/2026) añade `price_high`/`price_low` a `NasdaqTick`.
La guarda de inmutabilidad de CR-003 hace exactamente lo que se le pidió: **no
reescribe un timestamp que ya existe**. Eso significa que las velas ya ingeridas
con la firma de 3 campos no recibirían los dos campos nuevos, y el bucket
quedaría con dos esquemas distintos — sin poder calcular las features de rango
sobre el histórico.

La única salida honesta es reconstruir el bucket desde la fuente: los datos son
reproducibles (Twelve Data, QQQ, velas de 5 min), así que no se pierde nada que
no se pueda volver a obtener con la misma petición.

Seguridad de la operación
-------------------------
* Exige ``--confirm-wipe``. **Sin el flag no borra nada y sale con código 1.**
* Nunca se ejecuta por sí solo: no hay *default* de confirmación.
* Está fuera del alcance de RF-103/CR-003: la inmutabilidad protege el
  histórico *durante la operación normal*; esto es una migración consciente de
  un administrador, documentada en ``docs/change-requests.md`` (CR-001/CR-003).

Uso::

    .venv/bin/python -m src.ingestion.rebuild --dry-run       # solo informa
    .venv/bin/python -m src.ingestion.rebuild --confirm-wipe  # migra de verdad
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time
from dataclasses import dataclass, field

from influxdb_client import InfluxDBClient

from src.ingestion.backfill import run_backfill
from src.ingestion.engine import IngestionEngine
from src.ingestion.providers.twelve_data import TwelveDataProvider

logger = logging.getLogger(__name__)

_DELETE_START = "1970-01-01T00:00:00Z"
_DELETE_STOP = "2100-01-01T00:00:00Z"
# La API delete de InfluxDB 2.x usa sintaxis `tag=valor` (NO `==` como en Flux).
# Verificado en vivo contra v2.7.12: `"_measurement==X"` devuelve 400
# "bad tag value", `"_measurement=X"` devuelve 204.
_DELETE_PREDICATE = "_measurement=NasdaqTick"
# SDD §3.1.2 / SRS §4.2.2 — los cinco campos que deben quedar tras la migración.
_CAMPOS_ESPERADOS = ("price_open", "price_high", "price_low", "price_close", "volume")


@dataclass
class RebuildReport:
    antes_puntos: int = 0
    borrados: bool = False
    velas_obtenidas: int = 0
    velas_escritas: int = 0
    despues_puntos: int = 0
    campos_detectados: list[str] = field(default_factory=list)
    faltantes: list[str] = field(default_factory=list)
    segundos: float = 0.0


def _query(influx: InfluxDBClient, script: str) -> list[dict[str, object]]:
    """Ejecuta una consulta Flux cruda y devuelve las filas como dicts."""
    tables = influx.query_api().query(script, org=os.environ.get("INFLUXDB_ORG", "ritech"))
    filas: list[dict[str, object]] = []
    for table in tables:
        for record in table.records:
            filas.append(dict(record.values))
    return filas


def _count_puntos(influx: InfluxDBClient) -> int:
    filas = _query(
        influx,
        'from(bucket: "%s") |> range(start: 0)\n'
        '  |> filter(fn: (r) => r["_measurement"] == "NasdaqTick")\n'
        '  |> filter(fn: (r) => r["_field"] == "price_close")\n'
        '  |> count()' % os.environ.get("INFLUXDB_BUCKET", "nasdaq_ticks"),
    )
    return int(filas[0].get("_value", 0)) if filas else 0


def _campos_presentes(influx: InfluxDBClient) -> list[str]:
    filas = _query(
        influx,
        'from(bucket: "%s") |> range(start: 0)\n'
        '  |> filter(fn: (r) => r["_measurement"] == "NasdaqTick")\n'
        '  |> keep(columns: ["_field"]) |> distinct(column: "_field")'
        % os.environ.get("INFLUXDB_BUCKET", "nasdaq_ticks"),
    )
    return sorted({str(f["_field"]) for f in filas if f.get("_field")})


def _borrar_todo(influx: InfluxDBClient) -> None:
    bucket = os.environ.get("INFLUXDB_BUCKET", "nasdaq_ticks")
    org = os.environ.get("INFLUXDB_ORG", "ritech")
    influx.delete_api().delete(
        start=_DELETE_START, stop=_DELETE_STOP,
        predicate=_DELETE_PREDICATE, bucket=bucket, org=org,
    )


async def rebuild(
    influx: InfluxDBClient,
    provider: TwelveDataProvider,
    *,
    trading_days: int,
    confirm: bool,
    dry_run: bool,
) -> RebuildReport:
    """Borra los `NasdaqTick` y vuelve a ingerirlos con la firma de 5 campos."""
    if not confirm and not dry_run:
        raise SystemExit(
            "ABORTADO: se requiere --confirm-wipe para borrar los puntos "
            "`NasdaqTick` del bucket. Sin ese flag no se modifica nada."
        )

    started = time.perf_counter()
    report = RebuildReport()
    # De aquí en adelante toda consulta es de solo lectura, segura sin confirmación.
    report.antes_puntos = _count_puntos(influx)
    campos_antes = _campos_presentes(influx)
    report.campos_detectados = campos_antes
    report.faltantes = [c for c in _CAMPOS_ESPERADOS if c not in campos_antes]
    logger.info(
        "Estado previo: %d puntos, campos=%s, faltantes=%s",
        report.antes_puntos, campos_antes, report.faltantes,
    )

    if dry_run:
        logger.info("DRY-RUN: se borrarían %d puntos y se reingirían con BACKFILL_DAYS=%d.",
                    report.antes_puntos, trading_days)
        report.segundos = time.perf_counter() - started
        return report

    # Único punto destructivo del módulo — inalcanzable sin --confirm-wipe.
    try:
        _borrar_todo(influx)
    except Exception as exc:  # el borrado falló: abortar ANTES de reingresar
        raise SystemExit(
            f"ERROR: el borrado falló ({exc}). No se reingresa nada y el bucket "
            "queda intacto."
        ) from exc
    report.borrados = True
    restantes = _count_puntos(influx)
    logger.warning("Borrado completado: quedan %d puntos `NasdaqTick`.", restantes)
    if restantes:
        raise SystemExit(f"ERROR: quedaron {restantes} puntos tras el borrado; se detiene.")

    engine = IngestionEngine(influx, provider.api_key, provider=provider)
    backfill = await run_backfill(
        engine, provider, trading_days, end_ms=int(time.time() * 1000),
    )
    report.velas_obtenidas = backfill.velas_unicas
    report.velas_escritas = backfill.velas_escritas
    if backfill.errores:
        raise SystemExit(f"ERROR durante el re-ingreso: {backfill.errores}")

    report.despues_puntos = _count_puntos(influx)
    campos_despues = _campos_presentes(influx)
    report.campos_detectados = campos_despues
    report.faltantes = [c for c in _CAMPOS_ESPERADOS if c not in campos_despues]
    report.segundos = time.perf_counter() - started

    if report.faltantes:
        raise SystemExit(f"ERROR: siguen faltando campos tras la migración: {report.faltantes}")
    return report


def _print(report: RebuildReport, trading_days: int, dry_run: bool) -> None:
    print()
    print("=" * 68)
    print("MIGRACIÓN CR-001 — bucket de InfluxDB a la firma de 5 campos (SDD v1.1)")
    print("=" * 68)
    print(f"  Modo                      : {'DRY-RUN (sin cambios)' if dry_run else 'EJECUTADA'}")
    print(f"  Puntos antes              : {report.antes_puntos}")
    print(f"  Borrado                   : {'sí' if report.borrados else 'no'}")
    print(f"  Sesiones a reingresar     : {trading_days} (BACKFILL_DAYS)")
    print(f"  Velas obtenidas           : {report.velas_obtenidas}")
    print(f"  Velas escritas            : {report.velas_escritas}")
    print(f"  Puntos después            : {report.despues_puntos}")
    print(f"  Campos detectados         : {', '.join(report.campos_detectados) or '-'}")
    print(f"  Campos faltantes          : {', '.join(report.faltantes) or 'ninguno'}")
    print(f"  Duración                  : {report.segundos:.1f} s")
    print("=" * 68)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migración única del bucket a la firma de CR-001 (requiere --confirm-wipe).",
    )
    parser.add_argument("--confirm-wipe", action="store_true",
                        help="CONFIRMACIÓN EXPLÍCITA. Sin este flag no se borra nada.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Informe previo sin modificar el bucket.")
    parser.add_argument("--trading-days", type=int,
                        default=int(os.environ.get("BACKFILL_DAYS", "500")),
                        help="Sesiones bursátiles a reingresar (env BACKFILL_DAYS).")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    from src.security.crypto import decrypt_secret

    if args.dry_run and not args.confirm_wipe:
        # dry-run no necesita confirmación porque no toca nada, pero lo dejamos
        # explícito para que nadie confunda un informe con una migración.
        logger.info("Dry-run: no se requiere --confirm-wipe porque no se modifica nada.")

    api_key = decrypt_secret(os.environ["TWELVE_DATA_API_KEY_ENC"])
    influx = InfluxDBClient(
        url=os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086"),
        token=os.environ["INFLUXDB_ADMIN_TOKEN"],
        org=os.environ.get("INFLUXDB_ORG", "ritech"),
    )
    provider = TwelveDataProvider(api_key=api_key, symbol=IngestionEngine.SYMBOL)

    report = asyncio.run(rebuild(
        influx, provider,
        trading_days=args.trading_days,
        confirm=args.confirm_wipe,
        dry_run=args.dry_run,
    ))
    _print(report, args.trading_days, args.dry_run)
    influx.close()


if __name__ == "__main__":
    main()
