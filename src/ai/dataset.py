"""Construcción del dataset de entrenamiento desde InfluxDB (lado Máquina 2).

También **sin `torch`**: usa solo numpy + `influxdb-client`, ambos del grupo
`base`. Esto permite validar el dataset, la distribución de clases y la
ausencia de fuga sin tener la pila de IA instalada.

Ajustes del plan de Sprint 3 que implementa este módulo
------------------------------------------------------
1. **Muestras contenidas en una sesión.** Ninguna ventana ni su horizonte de
   etiqueta cruzan el cierre de mercado: las muestras se construyen dentro de
   cada sesión y las sesiones se detectan por hueco temporal, no por calendario.
2. **Sin fuga entre cortes.** Cortes temporales con **embargo de `EMBARGO` = 15
   velas** (12 de ventana + 3 de horizonte) entre train/val y entre val/test.
3. **Normalización solo de train.** Media y desviación se calculan sobre las
   muestras de train y viajan en el artefacto; test nunca las ve.
4. **Banda STABLE paramétrica.** Se informa la distribución real antes de
   fijarla; el artefacto guarda el valor congelado.
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field

import numpy as np
from influxdb_client import InfluxDBClient

from src.ai.features import (
    EMBARGO,
    FEATURE_NAMES,
    HORIZON,
    LABEL_NAMES,
    LABEL_STABLE,
    WINDOW,
    Candle,
    extract_window,
    forward_return_pct,
    label_for,
    max_samples_per_session,
    persistence_return_pct,
    session_of,
)

# Medición y bucket de SRS §4.2.2 / SDD §3.1.2.
_MEASUREMENT = "NasdaqTick"


@dataclass(frozen=True)
class DatasetConfig:
    """Hiperparámetros del dataset. Se fijan antes de entrenar y se guardan en el artefacto.

    Banda STABLE — **regla fija, declarada en `docs/plan-ia-sprint3.md` §1**
    ---------------------------------------------------------------------
    ``stable_band_pct`` es una **constante del sistema (±0,10 %) y no se
    recalcula en ningún reentrenamiento**, a pesar de que ±0,05 % equilibraría
    mejor las clases (1,2x frente a 3,2x).

    El motivo no es pereza: el modelo se reentrena **cada día** (RF-302), y si
    la banda se derivara de los cuantiles del train del día, el *significado*
    de ``STABLE`` cambiaría cada jornada. Dos filas con ``trend_prediction =
    'STABLE'`` en ``ai_predictions`` significarían cosas distintas según su
    fecha, y la tabla se retiene **5 años** para auditoría (RF-303). Una señal
    cuyo significado se mueve no es auditable, y el Motor Asimétrico la usa
    como decisión de entrada con apalancamiento 2x.

    Además, ensanchar la banda solo empeora el desbalance (ancho de banda y
    dominancia de ``STABLE`` son monótonos); la única vía para equilibrar es
    estrecharla hasta etiquetar como "merece operar" movimientos de 5 pb en
    15 min (0,29σ, por debajo del suelo de ruido del instrumento).

    Con ``STABLE`` en ~62 %, **la accuracy deja de ser una métrica útil**; la
    titular es el **macro-F1** (plan §1, "Consecuencia declarada").
    """

    window: int = WINDOW
    horizon: int = HORIZON
    stable_band_pct: float = 0.10
    train_frac: float = 0.70
    val_frac: float = 0.15
    # Sesiones con menos velas que esto no producen ni una muestra; se descartan
    # y se reportan en lugar de meter muestras truncadas al corte temporal.
    min_session_candles: int = WINDOW + HORIZON


@dataclass(frozen=True)
class Sample:
    """Una muestra: ventana de features + etiqueta + trazabilidad temporal."""

    x: np.ndarray              # (WINDOW, N_FEATURES) sin normalizar
    y: int                     # LABEL_DOWN | LABEL_STABLE | LABEL_UP
    session_idx: int
    window_start_idx: int      # índice global de la primera vela de la ventana
    label_end_idx: int         # índice global de la última vela del horizonte
    window_end_ts: int         # epoch ms del cierre de la ventana = momento de predicción
    label_end_ts: int          # epoch ms del cierre del horizonte
    forward_return_pct: float
    # Baseline 3 (plan §3): lo que predeciría la persistencia, con la MISMA
    # banda. Se calcula solo con velas anteriores al punto de predicción.
    persistence_y: int = LABEL_STABLE
    persistence_return_pct: float = 0.0

    @property
    def timestamp_prediccion(self) -> int:
        """Marca de tiempo con la que se persistiría en `ai_predictions.prediction_time`."""
        return self.window_end_ts


@dataclass(frozen=True)
class TrainStats:
    """Normalización calculada **solo** sobre train y congelada en el artefacto."""

    mean: np.ndarray           # (N_FEATURES,)
    std: np.ndarray            # (N_FEATURES,)

    def normalize(self, x: np.ndarray) -> np.ndarray:
        """Estandariza una ventana `(WINDOW, N_FEATURES)` o un lote apilado."""
        return ((x - self.mean) / self.std).astype(np.float32)


@dataclass
class Dataset:
    train: list[Sample]
    val: list[Sample]
    test: list[Sample]
    stats: TrainStats
    config: DatasetConfig
    session_start_idx: list[int] = field(default_factory=list)
    descartadas_cortas: int = 0

    @property
    def n_features(self) -> int:
        return len(FEATURE_NAMES)

    def sizes(self) -> dict[str, int]:
        return {"train": len(self.train), "val": len(self.val), "test": len(self.test)}

    def distribution(self, split: str) -> dict[str, int]:
        """Recuento de muestras por clase en un corte, siempre con las tres clases."""
        muestras = getattr(self, split)
        conteo = Counter(s.y for s in muestras)
        return {LABEL_NAMES[k]: conteo.get(k, 0) for k in sorted(LABEL_NAMES)}

    def matrix(self, split: str) -> tuple[np.ndarray, np.ndarray]:
        """Matriz `(N, WINDOW, N_FEATURES)` normalizada y vector de etiquetas."""
        muestras = getattr(self, split)
        if not muestras:
            return (np.empty((0, WINDOW, self.n_features), dtype=np.float32),
                    np.empty((0,), dtype=np.int64))
        x = self.stats.normalize(np.stack([s.x for s in muestras]))
        y = np.array([s.y for s in muestras], dtype=np.int64)
        return x, y


# ---------------------------------------------------------------------------
# Lectura de InfluxDB
# ---------------------------------------------------------------------------

def load_candles(
    influx: InfluxDBClient,
    *,
    bucket: str | None = None,
    org: str | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> list[Candle]:
    """Trae todas las velas `NasdaqTick` y las devuelve ordenadas.

    Usa un único `pivot` para pasar de series por campo a filas OHLCV, en lugar
    de una consulta por campo: tres consultas menos y ninguna posibilidad de que
    los campos queden desalineados en el tiempo.
    """
    bucket = bucket or os.environ.get("INFLUXDB_BUCKET", "nasdaq_ticks")
    org = org or os.environ.get("INFLUXDB_ORG", "ritech")
    rango = "range(start: 0)" if start_ts is None else (
        f"range(start: {start_ts}, stop: {end_ts})"
    )
    script = (
        f'from(bucket: "{bucket}") |> {rango}\n'
        f'  |> filter(fn: (r) => r["_measurement"] == "{_MEASUREMENT}")\n'
        "  |> pivot(rowKey: [\"_time\"], columnKey: [\"_field\"], valueColumn: \"_value\")"
    )

    velas: list[Candle] = []
    for tabla in influx.query_api().query(script, org=org):
        for rec in tabla.records:
            try:
                velas.append(Candle(
                    timestamp=int(rec.get_time().timestamp() * 1000),
                    open=float(rec.values["price_open"]),
                    high=float(rec.values["price_high"]),
                    low=float(rec.values["price_low"]),
                    close=float(rec.values["price_close"]),
                    volume=float(rec.values["volume"]),
                ))
            except (KeyError, TypeError, ValueError):
                # Vela incompleta (falta algún campo): se descarta y no contamina
                # el dataset. Se cuenta y se informa al construir.
                continue
    return sorted(velas, key=lambda c: c.timestamp)


# ---------------------------------------------------------------------------
# Construcción
# ---------------------------------------------------------------------------

def build_samples(sessions: list[list[Candle]], config: DatasetConfig) -> tuple[list[Sample], list[int], int]:
    """Construye las muestras de todas las sesiones.

    Devuelve `(muestras, índice_de_inicio_de_cada_sesión, sesiones_cortas)`.
    El índice de inicio es la posición de la primera vela de la sesión en la
    línea de tiempo concatenada, y es lo que permite aplicar el embargo de
    `EMBARGO` velas sin importar cuántas sesiones haya.
    """
    muestras: list[Sample] = []
    session_start: list[int] = []
    cortas = 0
    base = 0

    for s_idx, sesion in enumerate(sessions):
        session_start.append(base)
        n_utilizables = max_samples_per_session(len(sesion))
        if n_utilizables <= 0:
            cortas += 1
        for i in range(n_utilizables):
            ventana = sesion[i: i + WINDOW]
            retorno = forward_return_pct(sesion, i)
            retorno_previo = persistence_return_pct(sesion, i)
            muestras.append(Sample(
                x=extract_window(ventana),
                y=label_for(retorno, config.stable_band_pct),
                session_idx=s_idx,
                window_start_idx=base + i,
                label_end_idx=base + i + WINDOW - 1 + HORIZON,
                window_end_ts=ventana[-1].timestamp,
                label_end_ts=sesion[i + WINDOW - 1 + HORIZON].timestamp,
                forward_return_pct=retorno,
                persistence_y=label_for(retorno_previo, config.stable_band_pct),
                persistence_return_pct=retorno_previo,
            ))
        base += len(sesion)

    return muestras, session_start, cortas


def split_with_embargo(
    muestras: list[Sample],
    session_start: list[int],
    config: DatasetConfig,
) -> tuple[list[Sample], list[Sample], list[Sample]]:
    """Corte temporal 70/15/15 con embargo de `EMBARGO` velas hacia delante.

    Los cortes caen en frontera de sesión (ninguna sesión se parte entre
    train y val). El embargo descarta las `EMBARGO` primeras posiciones de
    ventana de val y de test, de modo que entre el último candle de un corte
    y el primero del siguiente quedan `EMBARGO` velas sin usar por ningún
    lado. Esas velas existen y no se tiran del dataset original: simplemente
    no forman parte de ninguna ventana de los cortes vecinos.
    """
    n_sesiones = len(session_start)
    if n_sesiones < 3:
        raise ValueError(
            f"se necesitan al menos 3 sesiones para cortar train/val/test, hay {n_sesiones}"
        )

    n_train = max(1, int(n_sesiones * config.train_frac))
    n_val = max(1, int(n_sesiones * config.val_frac))
    if n_train + n_val >= n_sesiones:  # dejar al menos una sesión de test
        n_train = max(1, n_sesiones - 2)
        n_val = 1

    corte_train = session_start[n_train]       # primera vela de val
    corte_val = session_start[n_train + n_val]  # primera vela de test

    train = [s for s in muestras if s.session_idx < n_train]
    val = [
        s for s in muestras
        if n_train <= s.session_idx < n_train + n_val
        and s.window_start_idx >= corte_train + EMBARGO
    ]
    test = [
        s for s in muestras
        if s.session_idx >= n_train + n_val
        and s.window_start_idx >= corte_val + EMBARGO
    ]

    if not (train and val and test):
        raise ValueError(
            f"un corte quedó vacío: train={len(train)} val={len(val)} test={len(test)} "
            f"(sesiones={n_sesiones}, train={n_train}, val={n_val})"
        )
    return train, val, test


def train_only_stats(train: list[Sample]) -> TrainStats:
    """Media y desviación por feature, calculadas **solo** sobre train.

    Se comparten entre las 12 posiciones de la ventana (10 estadísticos, no
    120): menos parámetros que guardar y ninguna ventaja demostrable en
    estandarizar por posición para un MLP plano.
    """
    if not train:
        raise ValueError("no hay muestras de train con las que calcular estadísticos")
    apilado = np.stack([s.x for s in train]).reshape(-1, len(FEATURE_NAMES))
    media = apilado.mean(axis=0)
    desviacion = np.maximum(apilado.std(axis=0), 1e-6)  # evita división por cero
    return TrainStats(mean=media.astype(np.float32), std=desviacion.astype(np.float32))


def build_dataset(candles: list[Candle], config: DatasetConfig | None = None) -> Dataset:
    """Construye el dataset completo: sesiones → muestras → cortes → stats."""
    config = config or DatasetConfig()
    sesiones = session_of(candles)
    muestras, session_start, cortas = build_samples(sessions=sesiones, config=config)
    train, val, test = split_with_embargo(muestras, session_start, config)
    return Dataset(
        train=train, val=val, test=test,
        stats=train_only_stats(train),
        config=config,
        session_start_idx=session_start,
        descartadas_cortas=cortas,
    )


def distribution_of(muestras: list[Sample]) -> dict[str, float]:
    """Distribución porcentual por clase; siempre las tres, aunque estén a cero."""
    conteo = Counter(s.y for s in muestras)
    total = len(muestras) or 1
    return {LABEL_NAMES[k]: conteo.get(k, 0) / total * 100.0 for k in sorted(LABEL_NAMES)}


def returns_of(muestras: list[Sample]) -> np.ndarray:
    """Retornos de las muestras, para analizar la banda STABLE antes de fijarla."""
    return np.array([s.forward_return_pct for s in muestras], dtype=float)


def distribution_at_band(returns: np.ndarray, band_pct: float) -> dict[str, int]:
    """Conteo de clases si se etiquetara `returns` con esa banda."""
    conteo = Counter(label_for(float(r), band_pct) for r in returns)
    return {LABEL_NAMES[k]: conteo.get(k, 0) for k in sorted(LABEL_NAMES)}
