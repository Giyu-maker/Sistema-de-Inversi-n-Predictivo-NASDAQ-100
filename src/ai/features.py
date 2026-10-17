"""Features del modelo predictivo — funciones puras sobre una ventana de 12 velas.

**Este módulo no importa `torch`.** Es código del lado de la Máquina 2: corre con
el grupo `base` de dependencias, sin la pila de IA instalada. `torch` solo aparece
en `model.py`, `train.py` y `xai.py`.

Tampoco importa `pydantic` ni `numpy` más allá de lo necesario: `Candle` es un
dataclass simple para no arrastrar validación al cálculo numérico.

Reglas que garantiza esta capa (RF-301, ajustes del plan Sprint 3)
-----------------------------------------------------------------
1. **Una ventana jamás cruza el cierre → apertura de dos sesiones.** Una ventana
   que contuviera velas de dos días distintos mezclaría el salto de la noche con
   el comportamiento intradía, y el modelo aprendería artefactos de horario, no
   mercado. `extract_window()` valida el hueco entre velas consecutivas y
   levanta `ValueError` si alguien intenta pasárselo.
2. **Todo lo que se calcula usa solo datos hasta la vela `t`.** Los indicadores
   son causales: no hay media móvil que mire hacia delante dentro de la ventana.

Ningún estadístico de normalización vive aquí: la media/desviación se calculan en
`dataset.py` **solo sobre train** y se guardan en el artefacto, porque si se
calcularan sobre el dataset completo habría fuga desde test hacia el modelo.

Formato de fecha del sistema: DD/MM/YYYY HH:mm:ss (convención 5.6).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# Ventana de entrada y horizonte de predicción (ajustes del plan Sprint 3).
WINDOW = 12          # velas observadas
HORIZON = 3          # velas proyectadas = 15 min
EMBARGO = WINDOW + HORIZON  # 15 velas mínimas entre train/val/test

#: Versión del esquema de features. **Cambia si cambia cualquier cosa que afecte
#: a los números de entrada**: el orden, el número o la definición de las
#: features, la ventana, o la banda STABLE. Viaja dentro del artefacto del
#: modelo y el servicio de inferencia de M1 la rechaza si no coincide con la suya
#: — un modelo entrenado con features distintas a las que le sirven no es un
#: modelo, es un número aleatorio con forma de predicción.
FEATURE_SCHEMA_VERSION = "1"

# Un hueco mayor que esto entre dos velas no es una vela siguiente: es el cierre
# de mercado. Todas las sesiones regulares de QQQ tienen huecos de 5 min dentro
# y de horas fuera; 60 min deja margen holgado para medias jornadas.
SESSION_GAP_MS = 60 * 60 * 1000

#: Nombres en orden. Deben coincidir con las columnas de `extract_window()` y con
#: lo que se persiste en el artefacto del modelo (RF-301: "feature schema").
FEATURE_NAMES: tuple[str, ...] = (
    "log_ret",     # log-retorno de la vela (vs. cierre anterior)
    "log_range",   # amplitud relativa de la vela: log(alto/bajo)
    "close_pos",   # posición del cierre dentro del rango [0,1]
    "body_pos",    # cuerpo firmado sobre el rango [-1,1]
    "log_vol",     # log del volumen
    "vol_rel",     # volumen relativo al medio de la ventana
    "ema_trend",   # (EMA rápida − EMA lenta) / cierre
    "range_vol",   # volatilidad por rango: media móvil de (alto−bajo)/cierre
    "ret_vol",     # volatilidad realizada: desviación móvil del log-retorno
    "from_open",   # recorrido acumulado desde la apertura de la ventana
)

N_FEATURES = len(FEATURE_NAMES)

# Hiperparámetros de las features: fijados ANTES de entrenar (plan Sprint 3).
# Se declaran aquí y no se tocan al comparar modelos: solo se ajustan
# weight_decay, dropout y early stopping, y eso en `train.py`.
EMA_FAST = 3
EMA_SLOW = 8
ROLLING_K = 6

_EPS = 1e-9

# Índices de las clases. Fijos de cara a artefacto y a la tabla `ai_predictions`.
LABEL_DOWN = 0
LABEL_STABLE = 1
LABEL_UP = 2
LABEL_NAMES: dict[int, str] = {LABEL_DOWN: "DOWN", LABEL_STABLE: "STABLE", LABEL_UP: "UP"}


@dataclass(frozen=True)
class Candle:
    """Vela de 5 minutos con los cinco campos de SRS §4.2.2 / SDD §3.1.2 (CR-001)."""

    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def _ema(x: np.ndarray, span: int) -> np.ndarray:
    """Media móvil exponencial causal: solo usa valores hasta la posición `t`."""
    alpha = 2.0 / (span + 1.0)
    out = np.empty_like(x, dtype=float)
    out[0] = x[0]
    for t in range(1, x.size):
        out[t] = alpha * x[t] + (1.0 - alpha) * out[t - 1]
    return out


def _rolling_mean(x: np.ndarray, k: int) -> np.ndarray:
    """Media móvil causal de los últimos `k` elementos (ventanas parciales al inicio)."""
    out = np.empty_like(x, dtype=float)
    for t in range(x.size):
        out[t] = x[max(0, t - k + 1): t + 1].mean()
    return out


def _rolling_std(x: np.ndarray, k: int) -> np.ndarray:
    """Desviación estándar causal de los últimos `k` elementos."""
    out = np.empty_like(x, dtype=float)
    for t in range(x.size):
        out[t] = x[max(0, t - k + 1): t + 1].std()
    return out


def validate_window(candles: list[Candle]) -> None:
    """Exige una ventana de `WINDOW` velas ordenadas y sin cruce de sesión.

    Regla 1 de este módulo: si dos velas vecinas están separadas por más de
    `SESSION_GAP_MS`, una terminó una sesión y la otra abrió otra. Eso es
    precisamente el cruce close→open que el plan de Sprint 3 prohíbe, así que se
    rechaza en lugar de dejar que contamine el dataset silenciosamente.
    """
    if len(candles) != WINDOW:
        raise ValueError(f"la ventana debe tener exactamente {WINDOW} velas, no {len(candles)}")
    for i in range(1, len(candles)):
        delta = candles[i].timestamp - candles[i - 1].timestamp
        if delta <= 0:
            raise ValueError(f"las velas no están ordenadas: delta={delta} en la posición {i}")
        if delta > SESSION_GAP_MS:
            raise ValueError(
                f"la ventana cruza el cierre de una sesión: hay {delta / 60_000:.0f} min "
                f"entre las velas {i - 1} y {i} (máximo permitido {SESSION_GAP_MS // 60_000} min)"
            )


def extract_window(candles: list[Candle]) -> np.ndarray:
    """Extrae la matriz de features `(WINDOW, N_FEATURES)` de una ventana.

    :raises ValueError: si la ventana no es de `WINDOW` velas o cruza sesiones.
    """
    validate_window(candles)

    o = np.array([c.open for c in candles], dtype=float)
    h = np.array([c.high for c in candles], dtype=float)
    lo = np.array([c.low for c in candles], dtype=float)
    cl = np.array([c.close for c in candles], dtype=float)
    vol = np.array([c.volume for c in candles], dtype=float)

    # Rango real de la vela; el mínimo evita división por cero en velas sin
    # amplitud (alto == bajo), que el proveedor puede emitir.
    rng = np.maximum(h - lo, _EPS)

    # log_ret: retorno logarítmico. La primera vela no tiene referencia previa
    # dentro de la ventana, así que usa su propia apertura — que es exactamente
    # el precio al que abrió la vela, no un dato futuro.
    reference = np.empty_like(cl)
    reference[0] = o[0]
    reference[1:] = cl[:-1]
    log_ret = np.log(np.maximum(cl, _EPS) / np.maximum(reference, _EPS))

    # CR-001: con alto/bajo persistidos existen las features de rango.
    log_range = np.log(np.maximum(h, _EPS) / np.maximum(lo, _EPS))
    close_pos = np.clip((cl - lo) / rng, 0.0, 1.0)
    body_pos = np.clip((cl - o) / rng, -1.0, 1.0)

    mean_vol = float(vol.mean())
    features = np.stack(
        [
            log_ret,
            log_range,
            close_pos,
            body_pos,
            np.log(np.maximum(vol, _EPS)),
            vol / max(mean_vol, _EPS),
            (_ema(cl, EMA_FAST) - _ema(cl, EMA_SLOW)) / np.maximum(cl, _EPS),
            _rolling_mean((h - lo) / np.maximum(cl, _EPS), ROLLING_K),
            _rolling_std(log_ret, ROLLING_K),
            (cl - o[0]) / np.maximum(o[0], _EPS),
        ],
        axis=1,
    )

    # Guarda de integridad: si una fila saliera con NaN/inf el modelo recibiría
    # basura sin aviso. Mejor explotar aquí que fallar dentro de la inferencia.
    if not np.isfinite(features).all():
        raise ValueError("las features contienen NaN o infinito: revisar las velas de entrada")
    return features


def session_of(candles: list[Candle]) -> list[list[Candle]]:
    """Agrupa velas en sesiones por hueco temporal.

    No depende del huso horario ni del día de calendario: una sesión nueva es
    cuando la distancia entre dos velas supera `SESSION_GAP_MS`. Así las medias
    jornadas (78 velas no siempre) se manejan solas y un cambio de huso no rompe
    nada — el mercado abre y cierra a la misma hora real aunque cambie el
    desfase de verano.

    Exige velas ordenadas y sin duplicados.
    """
    if not candles:
        return []
    ordered = sorted(candles, key=lambda c: c.timestamp)
    sesiones: list[list[Candle]] = [[ordered[0]]]
    for c in ordered[1:]:
        if c.timestamp == sesiones[-1][-1].timestamp:
            continue  # duplicado del proveedor: ignorar
        if c.timestamp - sesiones[-1][-1].timestamp > SESSION_GAP_MS:
            sesiones.append([])
        sesiones[-1].append(c)
    return [s for s in sesiones if s]


def forward_return_pct(session: list[Candle], start: int) -> float:
    """Variación porcentual de las próximas `HORIZON` velas desde la `start`.

    Se mide del cierre de la última vela de la ventana al cierre de la última
    vela del horizonte, que es la misma posición que usará el operador.
    """
    fin_ventana = start + WINDOW - 1
    fin_horizonte = fin_ventana + HORIZON
    base = session[fin_ventana].close
    return (session[fin_horizonte].close - base) / base * 100.0


def persistence_return_pct(session: list[Candle], start: int) -> float:
    """Variación de las `HORIZON` velas que **terminan** en el cierre de la ventana.

    Es el baseline de persistencia (plan §3, baseline 3): "si el movimiento
    reciente fue hacia arriba, asumo que sigue hacia arriba". Se mide desde el
    cierre de `HORIZON` velas antes hasta el cierre de la ventana — es decir,
    **solo con velas anteriores al punto de predicción**, por lo que no hay
    fuga y el baseline juega con las mismas reglas que el modelo.
    """
    fin_ventana = start + WINDOW - 1
    base = session[fin_ventana - HORIZON].close
    return (session[fin_ventana].close - base) / base * 100.0


def label_for(return_pct: float, band_pct: float) -> int:
    """Clasifica un retorno en DOWN / STABLE / UP con banda simétrica `band_pct`."""
    if return_pct > band_pct:
        return LABEL_UP
    if return_pct < -band_pct:
        return LABEL_DOWN
    return LABEL_STABLE


def max_samples_per_session(n_candles: int) -> int:
    """Muestras utilizables en una sesión de `n_candles` velas (≈64 con 78 velas)."""
    return max(0, n_candles - WINDOW - HORIZON + 1)


def window_to_vector(features: np.ndarray) -> np.ndarray:
    """Aplana `(WINDOW, N_FEATURES)` a un vector de entrada de la red."""
    return np.asarray(features, dtype=np.float32).reshape(-1)
