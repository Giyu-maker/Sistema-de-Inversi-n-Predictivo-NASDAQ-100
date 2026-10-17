"""Explicabilidad (XAI) — RF-303. Gradiente × entrada con torch puro.

**Sin SHAP ni Captum.** Ambos añadirían dependencias pesadas para un MLP de
120 entradas, donde gradiente × entrada ya da una atribución razonable y
completamente auditable: no hay aproximación estocástica, ni módulos de
fondo blanco, ni sorpresas de versionado en una intranet sin internet.

Qué se explica y qué no
-----------------------
RF-303 pide "lenguaje técnico-financiero" **que justifique la decisión para
auditorías**. Lo que produce este módulo es una **atribución de decisión**:
qué entradas pesaron en el resultado. **No es una causalidad de mercado.**

Esa distinción importa y está escrita en el propio texto del `rationale`: si el
modelo ponderó la posición del cierre dentro del rango, eso significa que esa
feature movió su logit, **no** que el mercado vaya a subir por estar el cierre
arriba del rango. Un auditor que confunda ambas cosas sacaría conclusiones
falsas de un informe técnicamente correcto.

Grupos de features (RF-303 pide lenguaje financiero, no nombres de variable)
---------------------------------------------------------------------------
Se agrupan en cuatro bloques con nombre de mercado para que el informe se lea
sin tener el código delante.
"""

from __future__ import annotations

import numpy as np
import torch
from torch import nn

from src.ai.features import FEATURE_NAMES, LABEL_NAMES, WINDOW

#: Grupos de features en lenguaje financiero. Cada feature pertenece a exactamente
#: uno; `test_los_grupos_cubren_todas_las_features` lo verifica en la suite.
FEATURE_GROUPS: dict[str, tuple[str, ...]] = {
    "retorno": ("log_ret",),
    "rango y volatilidad": ("log_range", "range_vol", "ret_vol"),
    "presión direccional": ("close_pos", "body_pos", "from_open", "ema_trend"),
    "volumen": ("log_vol", "vol_rel"),
}

_GROUP_OF: dict[str, str] = {
    feature: grupo for grupo, features in FEATURE_GROUPS.items() for feature in features
}

#: Nombre de mercado de cada feature, para redactar el `rationale` sin exponer
#: nombres de variable a un auditor. El ordenador lee `FEATURE_NAMES`; el que
#: audita lee esto.
FEATURE_LABELS: dict[str, str] = {
    "log_ret": "el retorno de la vela",
    "log_range": "la amplitud de la vela",
    "close_pos": "la posición del cierre dentro del rango",
    "body_pos": "el cuerpo de la vela sobre su amplitud",
    "log_vol": "el volumen negociado",
    "vol_rel": "el volumen relativo a la ventana",
    "ema_trend": "la tendencia entre medias móviles",
    "range_vol": "la volatilidad por rango",
    "ret_vol": "la volatilidad realizada",
    "from_open": "el recorrido acumulado desde la apertura",
}


def label_of(feature_name: str) -> str:
    """Nombre de mercado de una feature; levanta si no está catalogada."""
    try:
        return FEATURE_LABELS[feature_name]
    except KeyError:
        raise KeyError(f"la feature {feature_name!r} no tiene etiqueta financiera") from None


def group_of(feature_name: str) -> str:
    """Devuelve el grupo financiero de una feature; levanta si no está catalogada."""
    try:
        return _GROUP_OF[feature_name]
    except KeyError:
        raise KeyError(
            f"la feature {feature_name!r} no está asignada a ningún grupo de FEATURE_GROUPS"
        ) from None


def gradient_x_input(
    model: nn.Module,
    x: torch.Tensor,
    target: int,
    *,
    window: int = WINDOW,
    n_features: int = len(FEATURE_NAMES),
) -> np.ndarray:
    """Atribución gradiente × entrada hacia la clase `target`.

    Recibe la entrada **aplanada** como la consume la red, ``(N, WINDOW·F)``, y
    devuelve ``(N, WINDOW, N_FEATURES)``: cada entrada es el producto del
    gradiente de la salida respecto a esa entrada por el valor de la entrada
    normalizada, es decir cuánto y en qué dirección contribuyó a mover el logit
    de `target`.

    Se clona la entrada con ``requires_grad`` para no ensuciar el tensor que
    llama, y se limpian los gradientes antes de retropropagar.
    """
    entrada = x.detach().clone().float()
    if entrada.ndim == 1:
        entrada = entrada[None, :]
    if entrada.ndim != 2:
        raise ValueError(f"se esperaba una entrada aplanada (N, D), no de forma {tuple(entrada.shape)}")
    if entrada.shape[1] != window * n_features:
        raise ValueError(
            f"se esperaban {window * n_features} entradas aplanadas, no {entrada.shape[1]}"
        )
    entrada = entrada.requires_grad_(True)

    model.zero_grad(set_to_none=True)
    logits = model(entrada)
    objetivo = logits[target] if logits.ndim == 1 else logits[0, target]
    objetivo.backward()

    gradiente = entrada.grad
    if gradiente is None:
        raise RuntimeError("el modelo no produjo gradientes: ¿parámetros con requires_grad=False?")
    atribucion = (gradiente * entrada).detach().cpu().numpy()
    return atribucion.reshape(-1, window, n_features)


def attribution_by_group(
    atribucion: np.ndarray,
    feature_names: tuple[str, ...] = FEATURE_NAMES,
) -> dict[str, float]:
    """Peso absoluto agregado por grupo, en **porcentaje** del peso total.

    Se usa valor absoluto: un grupo puede empujar hacia arriba y hacia abajo a
    la vez, y para "qué ponderó el modelo" eso es un solo peso grande, no dos
    pesos que se cancelan.
    """
    if atribucion.shape[1] != len(feature_names):
        raise ValueError(
            f"la atribución tiene {atribucion.shape[1]} features y se esperaban {len(feature_names)}"
        )
    totales = {grupo: 0.0 for grupo in FEATURE_GROUPS}
    for j, nombre in enumerate(feature_names):
        totales[group_of(nombre)] += float(np.abs(atribucion[:, j]).sum())
    total = sum(totales.values())
    if total <= 0:
        # Sin atribución (red muerta o entrada exactamente en cero): reparto
        # uniforme en lugar de dividir por cero y emitir NaN a un auditor.
        uniforme = 100.0 / len(totales)
        return {g: uniforme for g in totales}
    return {g: v / total * 100.0 for g, v in totales.items()}


def attribution_by_position(atribucion: np.ndarray) -> np.ndarray:
    """Peso absoluto por vela de la ventana, en porcentaje del total."""
    totales = np.abs(atribucion).sum(axis=1)
    total = float(totales.sum())
    if total <= 0:
        return np.full(atribucion.shape[0], 100.0 / max(atribucion.shape[0], 1))
    return totales / total * 100.0


def _es(n: float, decimales: int = 1) -> str:
    """Formato numérico español: coma decimal. El rationale se guarda en TEXT para auditores."""
    return f"{n:.{decimales}f}".replace(".", ",")


def build_rationale(
    atribucion: np.ndarray,
    probabilities: np.ndarray,
    predicted_index: int,
    feature_names: tuple[str, ...] = FEATURE_NAMES,
    label_names: dict[int, str] = LABEL_NAMES,
) -> str:
    """Redacta el `technical_rationale` de RF-303 en lenguaje financiero.

    Describe **qué ponderó el modelo**, por grupo y por posición de la ventana,
    y termina con la advertencia explícita de que no es una causalidad de
    mercado. Esa advertencia no es decorativa: separa una atribución de
    decisión de una justificación de mercado, y un auditor debe poder leerlo
    sin tener el código delante.
    """
    grupos = attribution_by_group(atribucion, feature_names)
    orden = sorted(grupos.items(), key=lambda kv: kv[1], reverse=True)
    posiciones = attribution_by_position(atribucion)

    principal, peso_principal = orden[0]
    menor, peso_menor = orden[-1]

    # Se listan TODOS los grupos, no solo los extremos: a un auditor le importa
    # el reparto completo, y omitir los del medio obligaría a abrir el código.
    reparto = ", ".join(f"«{g}» {_es(p)} %" for g, p in orden)

    # Feature con más peso dentro del grupo principal.
    dentro = [j for j, n in enumerate(feature_names) if group_of(n) == principal]
    j_top = max(dentro, key=lambda j: float(np.abs(atribucion[:, j]).sum()))
    peso_feature = float(np.abs(atribucion[:, j_top]).sum()) / max(
        float(np.abs(atribucion).sum()), 1e-12
    ) * 100.0

    # Tres ventanas con más peso (1-based, para que se lea como "vela 12 de 12").
    top_pos = (np.argsort(posiciones)[::-1][:3] + 1).tolist()
    top_pos_txt = ", ".join(str(p) for p in sorted(top_pos))

    probs_txt = " / ".join(
        f"{label_names[i]} {_es(float(probabilities[i]) * 100, 1)} %"
        for i in sorted(label_names)
    )
    decision = label_names[predicted_index]
    confianza = float(probabilities[predicted_index]) * 100.0

    return (
        f"Atribución de la decisión (gradiente × entrada) sobre una ventana de "
        f"{WINDOW} velas. "
        f"Reparto del peso total por grupo: {reparto}. "
        f"El grupo de mayor peso fue «{principal}» ({_es(peso_principal)} %) y el "
        f"menor «{menor}» ({_es(peso_menor)} %). "
        f"Dentro de «{principal}», lo que más pesó fue "
        f"{label_of(feature_names[j_top])} ({_es(peso_feature)} % del total). "
        f"Las ventanas con más peso fueron la {top_pos_txt} de {WINDOW}, es decir "
        f"las más recientes de la ventana observada. "
        f"Probabilidades de clase: {probs_txt}. "
        f"Decisión: {decision} con {_es(confianza)} % de confianza. "
        f"Nota para auditoría (RF-303): este registro describe qué ponderó el "
        f"modelo en su decisión; no es una causalidad de mercado ni una "
        f"previsión justificada del comportamiento futuro del índice."
    )
