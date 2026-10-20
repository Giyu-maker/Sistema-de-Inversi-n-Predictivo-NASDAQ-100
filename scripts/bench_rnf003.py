"""Benchmark de RNF-003 — «procesar una vela nueva + IA + cálculo < 500 ms».

Mide, contra los servicios REALES de la Máquina 2 (InfluxDB, PostgreSQL y el
servicio de inferencia de M1), el tiempo por fase del ciclo de una vela:

    traer (Twelve Data) → validar → escribir en InfluxDB → leer la ventana
    → extraer features → inferencia en M1 → cálculo asimétrico → persistir
    la predicción en PostgreSQL

Dos límites que el informe debe recordar siempre, y por eso el script los
imprime con las cifras:

* La **quinta componente** de RNF-003 («actualizar la interfaz») no se mide
  aquí: el dashboard existe desde la Fase B2. El objetivo de 500 ms se
  evalúa por tanto sobre el backend y se reme al cierre de B2.
* La inferencia se mide con el modelo en **CPU** (este entorno de
  desarrollo corre en M2). En M1, con la GTX 1660 SUPER, el tiempo será
  distinto y se vuelve a medir al desplegar.

Limpieza (no deja nada):
* PostgreSQL: cada INSERT corre en su propia transacción sobre una tabla
  TEMPORAL (`LIKE ai_predictions`) que se ROLLBACK-ea; `ai_predictions` real
  no recibe ni una fila.
* InfluxDB: la fase de escritura usa velas sintéticas en 2001 (rango lejano
  e inconfundible) que se BORRAN al final, dentro de un `finally`. El
  histórico real ni se toca (CR-003 va por la guarda de la capa de
  aplicación, y aquí se miden escrituras nuevas de verdad).

Uso::

    set -a; source .env; source .env.local; set +a
    .venv/bin/python scripts/bench_rnf003.py                 # 50 iteraciones
    .venv/bin/python scripts/bench_rnf003.py --iteraciones 100 --sin-escritura
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import platform
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import psycopg2
from influxdb_client import InfluxDBClient

from src.ai.client import InferenceClient
from src.ai.features import extract_window
from src.ai.prediccion import (
    VELAS_A_CONSULTAR,
    a_candle,
    a_features_candle,
    ultima_ventana,
)
from src.calculator.asymmetric import AsymmetricCalculator
from src.database import pg
from src.database.influx_lectura import LectorInflux
from src.ingestion.engine import IngestionEngine
from src.security.crypto import decrypt_secret

OBJETIVO_MS = 500.0
INTERVALO_MS = 5 * 60 * 1000

#: Rango sintético para medir escritura real en InfluxDB (enero de 2001: no
#: puede confundirse con ningún dato de mercado del proyecto).
SINTETICA_INICIO_MS = int(
    datetime(2001, 1, 2, 10, 0, tzinfo=ZoneInfo("America/New_York")).timestamp() * 1000
)


def _percentil(valores: list[float], fraccion: float) -> float:
    ordenados = sorted(valores)
    indice = min(len(ordenados) - 1, max(0, math.ceil(fraccion * len(ordenados)) - 1))
    return ordenados[indice]


def _rfc3339(epoch_ms: int) -> str:
    """epoch ms → `2001-01-02T15:00:00Z`: el API de borrado de InfluxDB
    recusa números y exige cadenas RFC3339 (probado: 400 con enteros)."""
    return datetime.fromtimestamp(epoch_ms / 1000, tz=ZoneInfo("UTC")).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _muestra(valores: list[float]) -> tuple[float, float, float]:
    """(p50, p95, máx) en ms."""
    return (
        _percentil(valores, 0.50),
        _percentil(valores, 0.95),
        max(valores),
    )


class Fase:
    """Una fase del ciclo con sus duraciones en milisegundos."""

    def __init__(self, nombre: str, nota: str = "") -> None:
        self.nombre = nombre
        self.nota = nota
        self.mses: list[float] = []

    def tiempo(self, segundos: float) -> None:
        self.mses.append(segundos * 1000.0)


def _tabla(fases: list[Fase], total: Fase) -> None:
    ancho = max(len(f.nombre) for f in [*fases, total]) + 2
    print()
    print(f"{'FASE':<{ancho}}{'n':>4}{'p50 (ms)':>12}{'p95 (ms)':>12}{'máx (ms)':>12}")
    print("-" * (ancho + 40))
    for fase in [*fases, total]:
        if not fase.mses:
            print(f"{fase.nombre:<{ancho}}{'0':>4}{'—':>12}{'—':>12}{'—':>12}")
            if fase.nota:
                print(f"  ↳ {fase.nota}")
            continue
        p50, p95, maximo = _muestra(fase.mses)
        print(
            f"{fase.nombre:<{ancho}}{len(fase.mses):>4}"
            f"{p50:>12.2f}{p95:>12.2f}{maximo:>12.2f}"
        )
        if fase.nota:
            print(f"  ↳ {fase.nota}")


def _conexion_postgres():
    """Conexión propia con tabla temporal `LIKE ai_predictions`.

    No se usa `pg.conexion()` (contextmanager) porque confirma y cierra al
    salir: aquí la conexión tiene que vivir todo el bucle, con un ROLLBACK
    explícito por iteración. `INCLUDING CONSTRAINTS` copia los CHECK del
    esquema real (tendencia válida, confianza en [0,1], rationale no vacío),
    de modo que la fila del benchmark tiene que ser tan válida como una real.
    """
    try:
        conexion = psycopg2.connect(pg.dsn())
    except psycopg2.OperationalError as exc:
        print(f" No se pudo conectar a PostgreSQL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    conexion.autocommit = False
    with conexion.cursor() as cursor:
        cursor.execute(
            "CREATE TEMP TABLE bench_ai_predictions "
            "(LIKE ai_predictions INCLUDING DEFAULTS INCLUDING CONSTRAINTS)"
        )
    conexion.commit()
    return conexion


_INSERTAR_BENCH = """
    INSERT INTO bench_ai_predictions
        (prediction_time, trend_prediction, confidence_score, technical_rationale)
    VALUES (%s, %s, %s, %s)
    RETURNING id
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--iteraciones", type=int, default=50)
    parser.add_argument(
        "--sin-escritura", action="store_true",
        help="No mide la escritura en InfluxDB (omite también su limpieza).",
    )
    args = parser.parse_args()
    n = max(1, args.iteraciones)

    print("Benchmark RNF-003 — RiTech-NASDAQ100")
    print(f"  Fecha        : {datetime.now(ZoneInfo('America/Bogota')):%d/%m/%Y %H:%M:%S} (Colombia)")
    print(f"  Máquina      : {platform.node()} · {platform.machine()} · {platform.python_version()}")
    print(f"  Iteraciones  : {n}")
    print(f"  Objetivo     : < {OBJETIVO_MS:.0f} ms por vela (RNF-003)")

    api_key = decrypt_secret(os.environ["TWELVE_DATA_API_KEY_ENC"])
    influx = InfluxDBClient(
        url=os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086"),
        token=os.environ["INFLUXDB_ADMIN_TOKEN"],
        org=os.environ.get("INFLUXDB_ORG", "ritech"),
    )
    engine = IngestionEngine(influx, api_key)
    lector = LectorInflux(influx)
    cliente_ia = InferenceClient()

    fases = {
        "traer": Fase("Traer vela de Twelve Data", "muestra única: cuota Basic (8/min)"),
        "validar": Fase("Validar payload (RF-101)"),
        "escribir": Fase("Escribir vela en InfluxDB", "incluye la guarda insert-if-absent (CR-003)"),
        "leer": Fase("Leer la ventana de InfluxDB"),
        "features": Fase("Extraer features de la ventana"),
        "inferencia": Fase("Inferencia en M1 (HTTP /predict)"),
        "calculo": Fase("Cálculo asimétrico (RF-201/202)"),
        "persistir": Fase("Persistir predicción en PostgreSQL"),
    }
    total = Fase("TOTAL ciclo por vela (backend)")

    # -- 1) Traer y validar una vela real (una sola vez: la cuota no da más) ---
    t0 = time.perf_counter()
    payload = asyncio.run(engine.fetch_nasdaq_quote())
    fases["traer"].tiempo(time.perf_counter() - t0)
    if not engine.validate_payload(payload):
        print("Twelve Data devolvió un payload inválido; no se puede continuar.", file=sys.stderr)
        return 1

    # -- 2) Conexión PG con tabla temporal (rollback total, sin filas reales) ---
    conexion = _conexion_postgres()

    base_sintetica = SINTETICA_INICIO_MS
    try:
        # Pre-limpieza: aunque la corrida anterior se cortara a medias, el
        # rango sintético empieza vacío.
        influx.delete_api().delete(
            bucket=os.environ.get("INFLUXDB_BUCKET", "nasdaq_ticks"),
            start=_rfc3339(base_sintetica - INTERVALO_MS),
            stop=_rfc3339(base_sintetica + n * INTERVALO_MS + INTERVALO_MS),
            predicate='_measurement="NasdaqTick"',
        )

        # -- 3) Bucle único: cada iteración cronometra TODAS las fases --------
        for i in range(n):
            t_fase = time.perf_counter()
            engine.validate_payload(payload)
            fases["validar"].tiempo(time.perf_counter() - t_fase)

            if not args.sin_escritura:
                t_fase = time.perf_counter()
                asyncio.run(
                    engine.write_to_influx(
                        base_sintetica + i * INTERVALO_MS,
                        100.0, 101.0, 99.0, 100.5, 1_000.0,
                    )
                )
                fases["escribir"].tiempo(time.perf_counter() - t_fase)

            t_fase = time.perf_counter()
            velas = lector.ultimas_velas(VELAS_A_CONSULTAR)
            fases["leer"].tiempo(time.perf_counter() - t_fase)

            t_fase = time.perf_counter()
            # Mismo trazado que `predecir_y_persistir`: conversión → ventana →
            # matriz de features sin normalizar (la normaliza el modelo).
            preparada = ultima_ventana(a_features_candle(velas))
            if preparada is None:
                print("La sesión más reciente no tiene 12 velas: faltan datos.", file=sys.stderr)
                return 1
            vector = np.asarray(extract_window(preparada.ventana), dtype=np.float64)
            fases["features"].tiempo(time.perf_counter() - t_fase)

            t_fase = time.perf_counter()
            prediccion = cliente_ia.predecir(vector)
            fases["inferencia"].tiempo(time.perf_counter() - t_fase)

            t_fase = time.perf_counter()
            cierres = [a_candle(v).close for v in velas[-2:]]
            variacion = (cierres[1] - cierres[0]) / cierres[0] * 100.0
            AsymmetricCalculator.calculate_yield(variacion)
            AsymmetricCalculator.is_threshold_breached(cierres[0], cierres[1])
            fases["calculo"].tiempo(time.perf_counter() - t_fase)

            t_fase = time.perf_counter()
            with conexion.cursor() as cursor:
                cursor.execute(
                    _INSERTAR_BENCH,
                    (preparada.cierre + i, prediccion.trend_prediction,
                     prediccion.confidence_score, "benchmark RNF-003"),
                )
            conexion.rollback()  # ai_predictions real: ni una fila
            fases["persistir"].tiempo(time.perf_counter() - t_fase)

            escribir_ms = fases["escribir"].mses[-1] if fases["escribir"].mses else 0.0
            total.mses.append(
                fases["validar"].mses[-1]
                + escribir_ms
                + fases["leer"].mses[-1]
                + fases["features"].mses[-1]
                + fases["inferencia"].mses[-1]
                + fases["calculo"].mses[-1]
                + fases["persistir"].mses[-1]
            )
    finally:
        if not args.sin_escritura:
            # Limpieza del rango sintético, dentro de finally: aunque una fase
            # posterior falle, el histórico real queda como estaba.
            influx.delete_api().delete(
                bucket=os.environ.get("INFLUXDB_BUCKET", "nasdaq_ticks"),
                start=_rfc3339(base_sintetica - INTERVALO_MS),
                stop=_rfc3339(base_sintetica + n * INTERVALO_MS + INTERVALO_MS),
                predicate='_measurement="NasdaqTick"',
            )
        conexion.close()

    if args.sin_escritura:
        fases["escribir"].nota = "omitida por --sin-escritura"
        total.nota = "sin la escritura de InfluxDB"

    _tabla(list(fases.values()), total)

    # -- Veredicto -----------------------------------------------------------
    # RNF-003 (SRS §6.2, texto literal): «El tiempo necesario para procesar
    # una nueva vela de 5 minutos, consultar InfluxDB, ejecutar el modelo de
    # IA local, calcular la asimetría y actualizar la interfaz gráfica no
    # debe superar los 500 ms EN EL SERVIDOR LOCAL». Los 500 ms son del
    # servidor local: la traída a Twelve Data es red externa y se informa
    # aparte, sin sumarla al presupuesto.
    p50, p95, _ = _muestra(total.mses)
    con_fetch = fases["traer"].mses[0]
    print()
    print(f" TOTAL ciclo local (p50 {p50:.1f} ms · p95 {p95:.1f} ms · máx {_muestra(total.mses)[2]:.1f} ms)")
    print(f" Traída de Twelve Data, aparte (red externa, muestra única): {con_fetch:.1f} ms")
    veredicto = "CUMPLE" if p95 < OBJETIVO_MS else "NO CUMPLE"
    print(f" Veredicto RNF-003 (p95 del servidor local vs {OBJETIVO_MS:.0f} ms): {veredicto}")
    print("  ↳ PROVISIONAL: falta la 5.ª componente («actualizar la interfaz»),")
    print("    que solo existe desde la Fase B2; ahí se reme y se cierra RNF-003.")
    print()
    print(" Notas que acompañan siempre a esta cifra:")
    print("  · La inferencia corre en CPU (entorno de desarrollo en M2); en M1")
    print("    con la GTX 1660 SUPER el tiempo será distinto.")
    print("  · La traída es una muestra única: el plan Basic de Twelve Data")
    print("    cuesta 1 crédito por petición (8/min, 800/día).")
    print("  · PostgreSQL: cada INSERT se hizo sobre una tabla temporal y se")
    print("    ROLLBACK-eó; `ai_predictions` no recibió ninguna fila.")
    if not args.sin_escritura:
        print("  · InfluxDB: las velas sintéticas de 2001 se borraron al final.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
