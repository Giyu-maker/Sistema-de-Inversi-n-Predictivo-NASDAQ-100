"""Métricas, baselines y estimación de incertidumbre — **pre-registrados** en el plan §3–§5.

Este módulo no contiene `torch`: es puro numpy, para que las decisiones
analíticas se puedan revisar sin instalar el grupo `ai`. El entrenamiento vive
en `train.py`; aquí vive todo lo que se calcula **una vez fijado el plan** y que
por tanto no puede depender del resultado.

Tres reglas que este archivo materializa y que no se pueden doblar mirando test:

1. **La métrica titular es macro-F1**, no accuracy. Con `STABLE` en ~62 %, el
   baseline mayoritario alcanza esa accuracy sin hacer nada.
2. **La incertidumbre se estima con bootstrap por bloques de sesión.** Las
   muestras de una misma sesión comparten 11 de sus 12 velas, así que no son
   independientes y un intervalo binomial i.i.d. quedaría estimado varios
   órdenes de magnitud demasiado estrecho.
3. **Las comparaciones son por bootstrap pareado de la diferencia.** Si el
   intervalo incluye el 0, no hay ventaja demostrada, y así se reporta.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from src.ai.features import LABEL_DOWN, LABEL_NAMES, LABEL_STABLE, LABEL_UP

N_CLASES = len(LABEL_NAMES)

#: Intervalo de confianza fijo por el plan §4: percentiles 2,5 / 97,5.
PERCENTILES = (2.5, 97.5)

#: Réplicas mínimas del plan §4 (≥ 1.000). 2.000 da percentiles estables.
N_REPLICAS = 2_000

#: Tramos de confianza del plan §5. El último es cerrado a la derecha para no
#: dejar fuera una confianza de exactamente 1,0000.
TRAMOS_CONFIANZA: tuple[tuple[float, float], ...] = (
    (0.0, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0 + 1e-9),
)

#: Umbrales de los perfiles de riesgo del SDD §5.4.
UMBRALES_PERFIL: dict[str, float] = {
    "CONVENTIONAL": 0.85,
    "MODERATE": 0.75,
    "AGGRESSIVE": 0.65,
}


# ---------------------------------------------------------------------------
# Métricas básicas
# ---------------------------------------------------------------------------

def confusion_matrix(y_true: np.ndarray, y_pred: np.ndarray,
                     n_clases: int = N_CLASES) -> np.ndarray:
    """Matriz de confusión `(n_clases, n_clases)`: filas = real, columnas = predicho."""
    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)
    matriz = np.zeros((n_clases, n_clases), dtype=np.int64)
    for real in range(n_clases):
        mascara = y_true == real
        for predicho in range(n_clases):
            matriz[real, predicho] = int(np.sum(mascara & (y_pred == predicho)))
    return matriz


def f1_por_clase(y_true: np.ndarray, y_pred: np.ndarray,
                 n_clases: int = N_CLASES) -> np.ndarray:
    """F1 de cada clase por separado.

    Se itera sobre las `n_clases` fijas, no sobre las presentes en los datos:
    una clase que el modelo nunca predice debe contar como 0, no desaparecer
    del promedio. Es exactamente lo que distingue al macro-F1 de la accuracy.
    """
    matriz = confusion_matrix(y_true, y_pred, n_clases)
    tp = np.diag(matriz).astype(float)
    fp = matriz.sum(axis=0) - tp
    fn = matriz.sum(axis=1) - tp
    precision = np.divide(tp, tp + fp, out=np.zeros(n_clases), where=(tp + fp) > 0)
    recall = np.divide(tp, tp + fn, out=np.zeros(n_clases), where=(tp + fn) > 0)
    denominador = precision + recall
    return np.divide(2 * precision * recall, denominador,
                     out=np.zeros(n_clases), where=denominador > 0)


def macro_f1(y_true: np.ndarray, y_pred: np.ndarray, n_clases: int = N_CLASES) -> float:
    """Métrica titular del plan §1, promedio del F1 de las tres clases."""
    return float(f1_por_clase(y_true, y_pred, n_clases).mean())


def accuracy(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Se reporta **solo acompañada** del macro-F1: por sí sola no dice nada aquí."""
    y_true = np.asarray(y_true)
    return float((y_true == np.asarray(y_pred)).mean()) if y_true.size else 0.0


@dataclass(frozen=True)
class Metricas:
    """Paquete de métricas de un corte, para poder compararlos sin re-calcular."""

    macro_f1: float
    accuracy: float
    f1_por_clase: np.ndarray          # DOWN, STABLE, UP
    confusion: np.ndarray             # filas real, columnas predicho

    @classmethod
    def de(cls, y_true: np.ndarray, y_pred: np.ndarray,
           n_clases: int = N_CLASES) -> "Metricas":
        return cls(
            macro_f1=macro_f1(y_true, y_pred, n_clases),
            accuracy=accuracy(y_true, y_pred),
            f1_por_clase=f1_por_clase(y_true, y_pred, n_clases),
            confusion=confusion_matrix(y_true, y_pred, n_clases),
        )

    def como_dict(self) -> dict:
        return {
            "macro_f1": self.macro_f1,
            "accuracy": self.accuracy,
            "f1_por_clase": {LABEL_NAMES[i]: float(v)
                             for i, v in enumerate(self.f1_por_clase)},
            "confusion": self.confusion.tolist(),
        }


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------

def baseline_mayoritario(y_train: np.ndarray, n: int,
                         n_clases: int = N_CLASES) -> np.ndarray:
    """Baseline 1 (plan §3): siempre la clase más frecuente de **train**.

    Es el techo de la accuracy sin modelo. Su macro-F1 ronda 0,26 con la banda
    de ±0,10 %, lo que demuestra que superar al azar no demuestra nada.
    """
    y_train = np.asarray(y_train)
    cuenta = np.bincount(y_train, minlength=n_clases)
    return np.full(n, int(cuenta.argmax()), dtype=np.int64)


def baseline_azar_por_priors(y_train: np.ndarray, y_true: np.ndarray, *, semilla: int,
                             repeticiones: int = 1_000,
                             n_clases: int = N_CLASES) -> tuple[np.ndarray, float, tuple[float, float]]:
    """Baseline 2 (plan §3): clase muestreada según la distribución de **train**.

    Devuelve `(predicción_representativa, macro_f1_medio, ic_95)`.

    **Se evalúa contra `y_true`**, las etiquetas reales del corte. Medirlo
    contra cualquier otra cosa —la clase mayoritaria, por ejemplo— devuelve un
    número con apariencia de métrica que en realidad mide otra relación y que
    no se puede comparar con nada del informe.

    Se promedian `repeticiones` tiradas porque una sola tiene demasiada
    varianza para servir de referencia, y se devuelve además una tirada
    determinista (la primera, con la semilla fija) para poder pasarla por el
    mismo bootstrap pareado que el resto de baselines y compararlos con
    idéntico procedimiento.
    """
    y_train = np.asarray(y_train)
    y_true = np.asarray(y_true)
    cuenta = np.bincount(y_train, minlength=n_clases).astype(float)
    if cuenta.sum() == 0:
        raise ValueError("no hay muestras de train para estimar los priors")
    if len(y_true) == 0:
        raise ValueError("no hay etiquetas reales contra las que evaluar el baseline")
    priors = cuenta / cuenta.sum()

    rng = np.random.RandomState(semilla)
    valores = np.empty(repeticiones, dtype=float)
    representativa: np.ndarray | None = None
    for r in range(repeticiones):
        tirada = rng.choice(n_clases, size=len(y_true), p=priors)
        if r == 0:
            representativa = tirada
        valores[r] = macro_f1(y_true, tirada, n_clases)

    inferior, superior = np.percentile(valores, PERCENTILES)
    assert representativa is not None
    return representativa, float(valores.mean()), (float(inferior), float(superior))


def baseline_persistencia(muestras: Sequence) -> np.ndarray:
    """Baseline 3 (plan §3): la clase que predeciría \"si el movimiento reciente
    fue hacia arriba, asumo que sigue hacia arriba\".

    Usa `Sample.persistence_y`, que está calculado **solo con velas anteriores**
    al punto de predicción, con la misma banda ±0,10 % que el modelo. Es la
    referencia que de verdad hay que batir, porque es lo que haría un humano.
    """
    if not muestras:
        raise ValueError("no hay muestras para el baseline de persistencia")
    return np.asarray([m.persistence_y for m in muestras], dtype=np.int64)


def pesos_de_clase(y_train: np.ndarray, n_clases: int = N_CLASES) -> np.ndarray:
    """Pesos por frecuencia inversa, calculados **solo sobre train** (plan §2).

    Se declararon de antemano, no son un ajuste posterior. Devuelven la media
    ponderada en 1, para que la escala de la pérdida no se desplace respecto a
    la entropía cruzada sin ponderar.
    """
    y_train = np.asarray(y_train)
    cuenta = np.bincount(y_train, minlength=n_clases).astype(float)
    if (cuenta == 0).any():
        raise ValueError(
            f"faltan clases en train para calcular pesos: {cuenta.tolist()}"
        )
    pesos = cuenta.sum() / (n_clases * cuenta)
    return pesos / pesos.mean()


def corregir_posterior(probs: np.ndarray, pesos: np.ndarray) -> np.ndarray:
    """Deshace la reponderación de clases para devolver el posterior **real**.

    El modelo se entrena con entropía cruzada ponderada por frecuencia inversa
    ``w_c``. Eso no cambia a qué clase apunta la red, sino a qué escala: la
    softmax converge a

        q(c|x) ∝ w_c · p(c|x)

    con ``p`` el posterior que habría sin reponderar. Para exponer la
    probabilidad real basta deshacer el factor y renormalizar:

        p(c|x) ∝ q(c|x) / w_c

    **Sin parámetros.** No hay temperatura ni nada que ajustar: no se tocó nada
    contra val ni contra test. Con ``w = 1`` la función es la identidad, así que
    un modelo entrenado sin pesos no se altera.

    La confianza que se expone al usuario (RF-304) es **este** posterior
    corregido, no la softmax cruda. Las dos dicen cosas distintas: la cruda
    mide cuánto crece la red dado que la clase sobre-representada fue la que se
    muestreó con más peso; la corregida es la probabilidad bajo la distribución
    real de clases, que es la que usa el `RiskManager`.

    Acepta ``(C,)`` o ``(..., C)`` y conserva la forma.
    """
    probs = np.asarray(probs, dtype=np.float64)
    pesos = np.asarray(pesos, dtype=np.float64)

    if pesos.ndim != 1:
        raise ValueError(f"los pesos de clase deben ser un vector, no forma {pesos.shape}")
    if not np.isfinite(pesos).all() or (pesos <= 0).any():
        raise ValueError(f"los pesos de clase deben ser finitos y positivos: {pesos.tolist()}")
    if probs.shape[-1] != len(pesos):
        raise ValueError(
            f"la última dimensión de probs ({probs.shape[-1]}) no coincide con el "
            f"número de pesos ({len(pesos)})"
        )
    if probs.size == 0:
        return probs.astype(np.float32)

    corregido = probs / pesos
    total = corregido.sum(axis=-1, keepdims=True)
    if not np.isfinite(total).all() or (total <= 0).any():
        raise ValueError("la corrección de prior produjo probabilidades no normalizables")
    return (corregido / total).astype(np.float32)


# ---------------------------------------------------------------------------
# Bootstrap por bloques de sesión
# ---------------------------------------------------------------------------

def sesiones_de(muestras: Sequence) -> np.ndarray:
    """Índice de sesión de cada muestra, en el mismo orden."""
    if not muestras:
        raise ValueError("no hay muestras")
    return np.asarray([m.session_idx for m in muestras], dtype=np.int64)


def _indice_de_bloques(sesiones: np.ndarray) -> dict[int, np.ndarray]:
    return {int(s): np.flatnonzero(sesiones == s) for s in np.unique(sesiones)}


def bootstrap_bloques(y_true: np.ndarray, y_pred: np.ndarray, sesiones: np.ndarray, *,
                      n_replicas: int = N_REPLICAS, semilla: int = 20261017,
                      n_clases: int = N_CLASES) -> tuple[tuple[float, float], np.ndarray]:
    """IC del macro-F1 remuestreando **sesiones completas** con reemplazo.

    No se remuestrean muestras sueltas: dos muestras consecutivas comparten 11
    de sus 12 velas, así que tratarlas como independientes daría un intervalo
    ridículamente estrecho. Devuelve `((inferior, superior), valores)`.
    """
    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)
    if y_true.shape != y_pred.shape:
        raise ValueError("y_true e y_pred deben tener la misma forma")
    if y_true.size == 0:
        raise ValueError("no hay muestras que remuestrear")

    bloques = _indice_de_bloques(sesiones)
    unicas = np.array(sorted(bloques), dtype=np.int64)
    rng = np.random.RandomState(semilla)
    valores = np.empty(n_replicas, dtype=float)
    for r in range(n_replicas):
        elegidas = rng.choice(unicas, size=len(unicas), replace=True)
        idx = np.concatenate([bloques[int(s)] for s in elegidas])
        valores[r] = macro_f1(y_true[idx], y_pred[idx], n_clases)
    inferior, superior = np.percentile(valores, PERCENTILES)
    return (float(inferior), float(superior)), valores


def bootstrap_pareado(y_true: np.ndarray, pred_a: np.ndarray, pred_b: np.ndarray,
                      sesiones: np.ndarray, *, n_replicas: int = N_REPLICAS,
                      semilla: int = 20261017,
                      n_clases: int = N_CLASES) -> tuple[tuple[float, float], float]:
    """IC de la **diferencia** de macro-F1 entre dos modelos sobre el mismo remuestreo.

    Se usan las mismas sesiones elegidas para ambos: eso es lo que hace la
    comparación pareada y reduce la varianza. **Si el intervalo incluye el 0,
    no hay ventaja demostrada** y así se reporta (plan §4).
    """
    y_true = np.asarray(y_true)
    pred_a = np.asarray(pred_a)
    pred_b = np.asarray(pred_b)
    if not (y_true.shape == pred_a.shape == pred_b.shape):
        raise ValueError("y_true y ambas predicciones deben tener la misma forma")

    bloques = _indice_de_bloques(sesiones)
    unicas = np.array(sorted(bloques), dtype=np.int64)
    rng = np.random.RandomState(semilla)
    diferencias = np.empty(n_replicas, dtype=float)
    for r in range(n_replicas):
        elegidas = rng.choice(unicas, size=len(unicas), replace=True)
        idx = np.concatenate([bloques[int(s)] for s in elegidas])
        diferencias[r] = (macro_f1(y_true[idx], pred_a[idx], n_clases)
                          - macro_f1(y_true[idx], pred_b[idx], n_clases))
    inferior, superior = np.percentile(diferencias, PERCENTILES)
    punto = macro_f1(y_true, pred_a, n_clases) - macro_f1(y_true, pred_b, n_clases)
    return (float(inferior), float(superior)), float(punto)


def hay_ventaja_demostrada(intervalo: tuple[float, float]) -> bool:
    """Regla del plan §4: el intervalo de la diferencia no puede contener el 0."""
    return not (intervalo[0] <= 0.0 <= intervalo[1])


# ---------------------------------------------------------------------------
# Calibración y umbrales
# ---------------------------------------------------------------------------

def calibracion(y_true: np.ndarray, y_pred: np.ndarray, confianzas: np.ndarray, *,
                tramos: Sequence[tuple[float, float]] = TRAMOS_CONFIANZA) -> list[dict]:
    """Precisión observada frente a confianza declarada, por tramos (plan §5).

    Una confianza mal calibrada no sirve como umbral de negocio: si el modelo
    dice 80 % y acierta el 55 %, el RiskManager está cortando contra un número
    que no significa lo que dice.
    """
    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)
    confianzas = np.asarray(confianzas, dtype=float)
    filas: list[dict] = []
    for inferior, superior in tramos:
        mascara = (confianzas >= inferior) & (confianzas < superior)
        n = int(mascara.sum())
        if n == 0:
            filas.append({
                "tramo": f"[{inferior:.2f}, {superior:.2f})",
                "n": 0, "confianza_media": None,
                "precision_observada": None, "brecha": None,
            })
            continue
        confianza_media = float(confianzas[mascara].mean())
        precision = float((y_pred[mascara] == y_true[mascara]).mean())
        filas.append({
            "tramo": f"[{inferior:.2f}, {superior:.2f})",
            "n": n,
            "confianza_media": confianza_media,
            "precision_observada": precision,
            "brecha": precision - confianza_media,
        })
    return filas


def calibracion_por_clase(y_true: np.ndarray, y_pred: np.ndarray, confianzas: np.ndarray, *,
                          n_clases: int = N_CLASES) -> list[dict]:
    """Precisión y confianza media **por clase predicha** (plan §5).

    La calibración agregada puede esconder un sesgo: si el modelo está
    sobrediagnosticado en `UP` y sobrediagnosticado a la baja en `STABLE`, el
    promedio puede salir razonable. Separarlo por clase expone de qué lado está
    el error, que es lo que le importa al `RiskManager` antes de abrir posición.
    """
    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)
    confianzas = np.asarray(confianzas, dtype=float)
    filas: list[dict] = []
    for clase in range(n_clases):
        mascara = y_pred == clase
        n = int(mascara.sum())
        if n == 0:
            filas.append({"clase": LABEL_NAMES[clase], "n": 0,
                          "confianza_media": None, "precision_observada": None,
                          "brecha": None})
            continue
        confianza_media = float(confianzas[mascara].mean())
        precision = float((y_true[mascara] == clase).mean())
        filas.append({
            "clase": LABEL_NAMES[clase],
            "n": n,
            "confianza_media": confianza_media,
            "precision_observada": precision,
            "brecha": precision - confianza_media,
        })
    return filas


def tabla_umbrales(y_true: np.ndarray, y_pred: np.ndarray, confianzas: np.ndarray, *,
                   umbrales: Sequence[float] | None = None) -> list[dict]:
    """Qué pasa en test al imponer cada umbral de perfil de riesgo (plan §5).

    Para cada umbral: cuántas predicciones lo superan y **qué precisión tienen
    en `UP` y en `DOWN` por separado**, que es lo que importa al Motor
    Asimétrico — una precisión agregada podría esconder que el modelo acierta
    en subidas y falla sistemáticamente en bajadas.
    """
    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)
    confianzas = np.asarray(confianzas, dtype=float)
    if umbrales is None:
        umbrales = tuple(UMBRALES_PERFIL.values())

    filas: list[dict] = []
    for umbral in umbrales:
        mascara = confianzas >= umbral
        n = int(mascara.sum())
        fila: dict = {
            "umbral": umbral,
            "n_predicciones": n,
            "pct_del_total": n / len(confianzas) * 100.0 if len(confianzas) else 0.0,
            "precision_global": (float((y_pred[mascara] == y_true[mascara]).mean())
                                 if n else None),
            "n_up": 0, "n_down": 0, "precision_up": None, "precision_down": None,
        }
        if n:
            up = mascara & (y_pred == LABEL_UP)
            down = mascara & (y_pred == LABEL_DOWN)
            fila["n_up"] = int(up.sum())
            fila["n_down"] = int(down.sum())
            if up.any():
                fila["precision_up"] = float((y_true[up] == LABEL_UP).mean())
            if down.any():
                fila["precision_down"] = float((y_true[down] == LABEL_DOWN).mean())
        filas.append(fila)
    return filas


# ---------------------------------------------------------------------------
# Acierto direccional
# ---------------------------------------------------------------------------

def acierto_direccional(y_true: np.ndarray, y_pred: np.ndarray, sesiones: np.ndarray, *,
                        n_replicas: int = N_REPLICAS, semilla: int = 20261017,
                        n_clases: int = N_CLASES) -> dict:
    """Acierto **solo donde hay una decisión que acertar**, con su intervalo.

    Se miran únicamente las muestras en las que **tanto la verdad como la
    predicción son `UP` o `DOWN`**. Las `STABLE` se descartan de los dos lados:
    si el modelo dice "no pasa nada" y no pasó nada, eso no acredita que sepa
    hacia dónde se va el mercado, solo que no se movió. Contarlo como acierto
    direccional inflaría el resultado con casos que no son de dirección.

    El intervalo se estima con el **mismo bootstrap por bloques de sesión**
    pre-registrado en el plan §4: las ventanas solapan 11 de sus 12 velas y un
    intervalo binomial i.i.d. quedaría demasiado estrecho.
    """
    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)
    if y_true.shape != y_pred.shape:
        raise ValueError("y_true e y_pred deben tener la misma forma")
    if y_true.shape != sesiones.shape:
        raise ValueError("sesiones debe alinearse con y_true")

    direccional = np.isin(y_true, (LABEL_DOWN, LABEL_UP)) & \
        np.isin(y_pred, (LABEL_DOWN, LABEL_UP))
    n = int(direccional.sum())
    casos_verdad = int(np.isin(y_true, (LABEL_DOWN, LABEL_UP)).sum())
    if n == 0:
        return {"n": 0, "aciertos": 0, "ratio": None, "ic_95": None,
                "casos_direccionales_verdad": casos_verdad,
                "n_clases_direccionales": n_clases}

    aciertos = int((direccional & (y_true == y_pred)).sum())

    bloques = _indice_de_bloques(sesiones)
    unicas = np.array(sorted(bloques), dtype=np.int64)
    rng = np.random.RandomState(semilla)
    valores: list[float] = []
    for _ in range(n_replicas):
        elegidas = rng.choice(unicas, size=len(unicas), replace=True)
        idx = np.concatenate([bloques[int(s)] for s in elegidas])
        mascara = direccional[idx]
        k = int(mascara.sum())
        if k == 0:
            continue  # réplica sin casos direccionales: no aporta información
        valores.append(float((y_true[idx][mascara] == y_pred[idx][mascara]).mean()))

    if valores:
        inferior, superior = np.percentile(np.asarray(valores), PERCENTILES)
        intervalo: tuple[float, float] | None = (float(inferior), float(superior))
    else:
        intervalo = None

    return {
        "n": n,
        "aciertos": aciertos,
        "ratio": aciertos / n,
        "ic_95": list(intervalo) if intervalo else None,
        # Solo la verdad direccional: es contra eso que se mira la cobertura.
        "casos_direccionales_verdad": casos_verdad,
        "n_clases_direccionales": n_clases,
    }


# ---------------------------------------------------------------------------
# Desplazamiento de régimen
# ---------------------------------------------------------------------------

def metricas_por_tercios(y_true: np.ndarray, y_pred: np.ndarray, *, n_tercios: int = 3,
                         n_clases: int = N_CLASES) -> list[dict]:
    """Macro-F1 por tercios **cronológicos** del corte (plan §6).

    El test es más plano que train y val, y eso es desplazamiento real de
    mercado. Cortarlo por tercios permite ver si el deterioro es uniforme o
    concentrado en el tramo más reciente, que es el que anticipa producción.
    """
    y_true = np.asarray(y_true)
    y_pred = np.asarray(y_pred)
    if y_true.size == 0:
        raise ValueError("no hay muestras que dividir")
    trozos = np.array_split(np.arange(y_true.size), n_tercios)
    filas: list[dict] = []
    for i, idx in enumerate(trozos):
        if idx.size == 0:
            continue
        filas.append({
            "tercio": i + 1,
            "n": int(idx.size),
            "macro_f1": macro_f1(y_true[idx], y_pred[idx], n_clases),
            "accuracy": accuracy(y_true[idx], y_pred[idx]),
            "down": float((y_true[idx] == LABEL_DOWN).mean()),
            "stable": float((y_true[idx] == LABEL_STABLE).mean()),
            "up": float((y_true[idx] == LABEL_UP).mean()),
        })
    return filas
