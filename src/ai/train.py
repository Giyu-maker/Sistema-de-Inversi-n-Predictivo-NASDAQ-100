"""CLI de entrenamiento y reentrenamiento diario — RF-302.

Corre en la **Máquina 2** con el grupo `ai` instalado. El artefacto que produce
se copia a la Máquina 1, que lo sirve por HTTP. **M1 no entrena y no escribe en
PostgreSQL.** El entrenamiento es local y no envía nada fuera del servidor
(REST-02).

Lo que está fijado antes de la primera época
--------------------------------------------
Todos los hiperparámetros están declarados en `Entrenamiento` y coinciden con
el plan §2. **La banda STABLE no es un argumento de la línea de comandos a
dedo**: no se puede cambiar sin editar el plan. Es deliberado — la banda es una
constante del sistema, no un parámetro de experimentación, y exponerla en la
CLI invitaría a moverla hasta que las métricas gusten.

Procedimiento, en orden
-----------------------
1. Se construye el dataset y se comprueba que el embargo no haya dejado ningún
   corte vacío.
2. Se fija la semilla y se calculan los pesos de clase **solo sobre train**.
3. Se entrena el baseline lineal y el MLP con **el mismo procedimiento**:
   AdamW, los mismos lotes, las mismas épocas y el mismo early stopping sobre
   val macro-F1.
4. Se evalúa test **una única vez**, al final. Ninguna decisión del informe
   retroalimenta el entrenamiento.
5. Se guarda el artefacto versionado y el informe en JSON.

Reentrenamiento automático (sin programador nuevo)
--------------------------------------------------
No se añade ningún planificador al sistema. El reentrenamiento diario de RF-302
se monta con lo que ya existe en CachyOS — un *timer* de systemd — y se instala
en `deploy/m1/` durante el PASO 3:

    # /etc/systemd/system/ritech-retrain.timer
    [Unit]
    Description=Reentrenamiento diario del modelo RiTech

    [Timer]
    # 21:30 EST = 20:30 hora Colombia; después del cierre de jornada (RF-302).
    OnCalendar=*-*-* 20:30:00 America/Bogota
    Persistent=true

    [Install]
    WantedBy=timers.target

`Persistent=true` es lo que cubre el caso en que la Máquina 2 estuviera
apagada a esa hora: al arrancar ejecuta el reentrenamiento omitido. El
equivalente en un cron sería la misma línea con `CRON_TZ=America/Bogota`.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch import nn

from src.ai.dataset import Dataset, DatasetConfig, build_dataset, load_candles
from src.ai.features import (
    FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION,
    HORIZON,
    LABEL_NAMES,
    WINDOW,
)
from src.ai.metrics import (
    UMBRALES_PERFIL,
    Metricas,
    acierto_direccional,
    baseline_azar_por_priors,
    baseline_mayoritario,
    baseline_persistencia,
    bootstrap_bloques,
    bootstrap_pareado,
    calibracion,
    calibracion_por_clase,
    corregir_posterior,
    hay_ventaja_demostrada,
    macro_f1,
    metricas_por_tercios,
    pesos_de_clase,
    sesiones_de,
    tabla_umbrales,
)
from src.ai.model import (
    DEFAULT_LINEAR_CONFIG,
    DEFAULT_MODEL_CONFIG,
    NasdaqLinear,
    NasdaqMLP,
    build_artifact,
    save_artifact,
)

SEMILLA_DEFECTO = 20261017
MODELOS_DIR = Path("models")

#: Arquitecturas entrenables. Se guardan **las dos** en cada corrida y la
#: configuración decide cuál queda activa, para que cambiar de opinión sea un
#: ajuste de entorno y no un reentrenamiento.
ARQUITECTURAS = ("mlp", "linear")


@dataclass(frozen=True)
class Entrenamiento:
    """Hiperparámetros **congelados** en el plan §2. No se tocan sin cambiar el plan."""

    lr: float = 1e-3
    weight_decay: float = 1e-4
    batch_size: int = 256
    max_epochs: int = 200
    paciencia: int = 20          # early stopping sobre val macro-F1
    semilla: int = SEMILLA_DEFECTO
    dropout: float = 0.20        # solo aplica al MLP; el lineal no tiene capas ocultas


# ---------------------------------------------------------------------------
# Entrenamiento
# ---------------------------------------------------------------------------

def fijar_semilla(semilla: int) -> None:
    """Fija las semillas de numpy y torch para que el resultado sea reproducible."""
    np.random.seed(semilla)
    torch.manual_seed(semilla)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(semilla)


def _aplanar(X: np.ndarray, ds: Dataset) -> np.ndarray:
    """`(N, WINDOW, F)` → `(N, WINDOW·F)` como consume la red.

    Se pasa el número de columnas explícito en vez de `-1`: con `N = 0`, `reshape`
    no puede inferir la dimensión y lanza un error de numpy que tapa el motivo
    real, que es que el corte está vacío.
    """
    return np.ascontiguousarray(X.reshape(len(X), WINDOW * ds.n_features), dtype=np.float32)


def _preparar_lotes(ds: Dataset, dispositivo: str) -> tuple[torch.Tensor, ...]:
    def apilar(split: str) -> tuple[torch.Tensor, torch.Tensor]:
        X, y = ds.matrix(split)
        return (torch.from_numpy(_aplanar(X, ds)).to(dispositivo),
                torch.from_numpy(y.astype(np.int64)).to(dispositivo))

    xtr, ytr = apilar("train")
    xva, yva = apilar("val")
    return xtr, ytr, xva, yva


def entrenar(modelo: nn.Module, ds: Dataset, cfg: Entrenamiento, pesos: np.ndarray,
             dispositivo: str) -> tuple[nn.Module, list[dict], float]:
    """Entrena con el procedimiento fijado en el plan §2.

    El criterio de parada anticipada es el **val macro-F1**, la misma métrica
    titular del informe; usar la pérdida en val elegiría un modelo distinto del
    que se va a reportar. Devuelve `(modelo_mejor, historial, mejor_macro_f1_val)`.
    """
    if not ds.train or not ds.val:
        raise ValueError(
            f"no se puede entrenar con cortes vacíos: train={len(ds.train)} "
            f"val={len(ds.val)}"
        )

    xtr, ytr, xva, yva = _preparar_lotes(ds, dispositivo)

    criterio = nn.CrossEntropyLoss(
        weight=torch.tensor(pesos, dtype=torch.float32, device=dispositivo)
    )
    optimizador = torch.optim.AdamW(
        modelo.parameters(), lr=cfg.lr, weight_decay=cfg.weight_decay
    )
    modelo.to(dispositivo)

    mejor_estado: dict[str, torch.Tensor] | None = None
    mejor_f1 = -1.0
    sin_mejora = 0
    historial: list[dict] = []

    for epoca in range(1, cfg.max_epochs + 1):
        modelo.train()
        permutacion = torch.randperm(len(xtr), device=dispositivo)
        perdida_acumulada = 0.0
        for i in range(0, len(permutacion), cfg.batch_size):
            lote = permutacion[i: i + cfg.batch_size]
            optimizador.zero_grad(set_to_none=True)
            perdida = criterio(modelo(xtr[lote]), ytr[lote])
            perdida.backward()
            optimizador.step()
            perdida_acumulada += float(perdida.item()) * len(lote)

        modelo.eval()
        with torch.no_grad():
            logits_val = modelo(xva)
            perdida_val = float(criterio(logits_val, yva).item())
            pred_val = logits_val.argmax(dim=1).cpu().numpy()
        f1_val = macro_f1(yva.cpu().numpy(), pred_val)

        historial.append({
            "epoca": epoca,
            "perdida_train": perdida_acumulada / len(xtr),
            "perdida_val": perdida_val,
            "macro_f1_val": f1_val,
        })

        if f1_val > mejor_f1 + 1e-6:
            mejor_f1 = f1_val
            sin_mejora = 0
            mejor_estado = {k: v.detach().clone() for k, v in modelo.state_dict().items()}
        else:
            sin_mejora += 1
            if sin_mejora >= cfg.paciencia:
                break

    if mejor_estado is None:
        raise RuntimeError("no se guardó ningún estado del modelo")
    modelo.load_state_dict(mejor_estado)
    modelo.eval()  # dropout apagado: la inferencia debe ser determinista
    return modelo, historial, mejor_f1


def _softmax(modelo: nn.Module, ds: Dataset, split: str, dispositivo: str) -> np.ndarray:
    """Softmax **cruda** de la red, `(N, 3)` en orden DOWN/STABLE/UP.

    Es el posterior bajo los pesos con que se entrenó, no el real. Se expone
    separado de `predecir` porque la decisión se toma aquí y la confianza se
    lee de otro sitio, y mezclarlas sin decirlo es como se llega a reportar un
    número que mide otra cosa.
    """
    X, _ = ds.matrix(split)
    if len(X) == 0:
        return np.zeros((0, len(LABEL_NAMES)), dtype=np.float32)
    entrada = torch.from_numpy(_aplanar(X, ds)).to(dispositivo)
    modelo.eval()
    with torch.no_grad():
        return torch.softmax(modelo(entrada), dim=1).cpu().numpy()


def predecir(modelo: nn.Module, ds: Dataset, split: str, dispositivo: str,
             class_weights: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Devuelve `(etiquetas_predichas, confianza_corregida)` para un corte.

    Dos cosas distintas, y la distinción importa:

    - **La etiqueta** es el `argmax` de la softmax cruda: la decisión de la red,
      la misma que se evaluó en las métricas de clasificación.
    - **La confianza** es el posterior corregido por prior de esa etiqueta,
      `p ∝ softmax / w_c` renormalizado con los pesos de train. **No es la
      softmax cruda.**

    **El modelo no aplica ningún umbral**: eso es del `RiskManager`.
    """
    crudas = _softmax(modelo, ds, split, dispositivo)
    if len(crudas) == 0:
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=float)
    predichas = crudas.argmax(axis=1).astype(np.int64)
    corregidas = corregir_posterior(crudas, class_weights)
    confianza = corregidas[np.arange(len(corregidas)), predichas]
    return predichas, confianza


def entrenar_lineal(ds: Dataset, cfg: Entrenamiento, pesos: np.ndarray,
                    dispositivo: str) -> tuple[nn.Module, list[dict], float]:
    """Baseline 4 (plan §3): una sola capa `Linear(120 → 3)`, mismo procedimiento.

    Su existencia responde a una pregunta: ¿el MLP justifica su complejidad? Si
    no lo supera, se reporta tal cual y se propone quedarse con este.

    Devuelve `(modelo, historial, mejor_macro_f1_val)`: el historial permite
    comprobar que el baseline recibió idéntico trato que el MLP.
    """
    fijar_semilla(cfg.semilla)
    modelo = NasdaqLinear(
        input_dim=int(DEFAULT_MODEL_CONFIG["input_dim"]),
        n_classes=len(LABEL_NAMES),
    )
    return entrenar(modelo, ds, cfg, pesos, dispositivo)


def entrenar_mlp(ds: Dataset, cfg: Entrenamiento, pesos: np.ndarray,
                 dispositivo: str) -> tuple[nn.Module, list[dict], float]:
    """MLP 120 → 64 → 32 → 3 con ReLU y dropout (plan §2)."""
    fijar_semilla(cfg.semilla)
    modelo = NasdaqMLP(
        input_dim=int(DEFAULT_MODEL_CONFIG["input_dim"]),
        hidden1=64,
        hidden2=32,
        n_classes=len(LABEL_NAMES),
        dropout=cfg.dropout,
    )
    return entrenar(modelo, ds, cfg, pesos, dispositivo)


# ---------------------------------------------------------------------------
# Informe
# ---------------------------------------------------------------------------

def _comparar(y_true: np.ndarray, pred_modelo: np.ndarray, pred_baseline: np.ndarray,
              sesiones: np.ndarray, *, replicas: int, semilla: int) -> dict:
    """Comparación pareada del plan §4 contra un baseline concreto."""
    intervalo, diferencia = bootstrap_pareado(
        y_true, pred_modelo, pred_baseline, sesiones,
        n_replicas=replicas, semilla=semilla,
    )
    return {
        "macro_f1_modelo": macro_f1(y_true, pred_modelo),
        "macro_f1_baseline": macro_f1(y_true, pred_baseline),
        "diferencia": diferencia,
        "ic_95_diferencia": list(intervalo),
        "ventaja_demostrada": hay_ventaja_demostrada(intervalo),
    }


def construir_informe(ds: Dataset, modelo_mlp: nn.Module, modelo_lineal: nn.Module,
                      cfg: Entrenamiento, pesos: np.ndarray, dispositivo: str, *,
                      replicas: int) -> dict[str, Any]:
    """Junta todo el análisis pre-registrado. Ningún número se ajusta contra test.

    **Las etiquetas verdaderas salen de `ds`, nunca de las predicciones.**
    Confundirlas aquí —fácil de hacer porque `predecir` devuelve
    `(predichas, confianza)`— inflaría el informe entero: las precisiones
    darían 1,0, el macro-F1 por tercios saldría perfecto y el modelo parecería
    infalible contra sí mismo. Se comprueba la longitud como red de seguridad.
    """
    reales = {s: np.asarray([m.y for m in getattr(ds, s)], dtype=np.int64)
              for s in ("train", "val", "test")}
    sesiones_test = sesiones_de(ds.test)
    sesiones_val = sesiones_de(ds.val)

    pred_mlp = {s: predecir(modelo_mlp, ds, s, dispositivo, pesos)
                for s in ("train", "val", "test")}
    pred_lineal = {s: predecir(modelo_lineal, ds, s, dispositivo, pesos)
                   for s in ("train", "val", "test")}

    # Cuántas veces la corrección de prior cambia la clase ganadora. Se reporta
    # por honestidad: la decisión se toma sobre la softmax cruda y la confianza
    # sobre la corregida, y el lector debe poder ver cuánto divergen.
    crudas_test = _softmax(modelo_mlp, ds, "test", dispositivo)
    corregidas_test = corregir_posterior(crudas_test, pesos)
    divergencia = (int((corregidas_test.argmax(axis=1) != crudas_test.argmax(axis=1)).sum())
                   if len(crudas_test) else 0)

    y_val = reales["val"]
    pred_val_mlp = pred_mlp["val"][0]
    conf_val = pred_mlp["val"][1]
    y_test = reales["test"]
    pred_test_mlp = pred_mlp["test"][0]
    conf_test = pred_mlp["test"][1]
    pred_test_lineal = pred_lineal["test"][0]
    if len(y_test) != len(pred_test_mlp) or len(y_test) != len(pred_test_lineal):
        raise ValueError(
            f"desalineación entre reales y predichos: {len(y_test)} / "
            f"{len(pred_test_mlp)} / {len(pred_test_lineal)}"
        )

    # --- baselines sobre test ---
    moda = int(np.bincount(reales["train"], minlength=len(LABEL_NAMES)).argmax())
    mayoritario = np.full(len(y_test), moda, dtype=np.int64)
    azar_pred, azar_media, azar_ic = baseline_azar_por_priors(
        reales["train"], y_test, semilla=cfg.semilla
    )
    persistencia = baseline_persistencia(ds.test)

    comparaciones = {
        "mayoritario": _comparar(y_test, pred_test_mlp, mayoritario, sesiones_test,
                                 replicas=replicas, semilla=cfg.semilla),
        "azar_por_priors": _comparar(y_test, pred_test_mlp, azar_pred, sesiones_test,
                                     replicas=replicas, semilla=cfg.semilla),
        "persistencia": _comparar(y_test, pred_test_mlp, persistencia, sesiones_test,
                                  replicas=replicas, semilla=cfg.semilla),
        "lineal": _comparar(y_test, pred_test_mlp, pred_test_lineal, sesiones_test,
                            replicas=replicas, semilla=cfg.semilla),
    }
    comparaciones["azar_por_priors"]["macro_f1_medio_1000_tiradas"] = azar_media
    comparaciones["azar_por_priors"]["ic_95_tiradas"] = list(azar_ic)

    # --- incertidumbre del propio modelo ---
    intervalo_test, _ = bootstrap_bloques(
        y_test, pred_test_mlp, sesiones_test, n_replicas=replicas, semilla=cfg.semilla
    )

    return {
        "modelo": {
            # La especificación, no el rendimiento: SRS §5.3 y SDD §5.3.2 exigen
            # una red neuronal. El lineal se entrena y se reporta igual como
            # referencia, y queda seleccionable con MODEL_ARCH.
            "tipo": "mlp",
            "seleccion_por": "especificacion (SRS §5.3, SDD §5.3.2): requiere red neuronal",
            "arquitectura": f"{WINDOW * len(FEATURE_NAMES)}→64→32→{len(LABEL_NAMES)}",
            "parametros": sum(p.numel() for p in modelo_mlp.parameters()),
            "dispositivo_entrenamiento": dispositivo,
            "torch_version": str(torch.__version__),
            "python": platform.python_version(),
        },
        # La confianza NO es la softmax cruda. Ver `corregir_posterior`.
        "confianza": {
            "tipo": "posterior_corregido_por_prior",
            "descripcion": (
                "p(c|x) ∝ softmax(c|x) / w_c, renormalizada con los pesos de clase "
                "del train. Es un cambio de escala sin parámetros: no se ajustó "
                "nada contra val ni contra test."
            ),
            "softmax_cruda_expuesta": False,
            "divergencia_contra_argmax_corregido": {
                "muestras": len(crudas_test),
                "cambios_de_clase": divergencia,
                "fraccion": (divergencia / len(crudas_test)) if len(crudas_test) else None,
            },
        },
        "esquema_features": FEATURE_SCHEMA_VERSION,
        "hiperparametros": asdict(cfg),
        "pesos_de_clase": {LABEL_NAMES[i]: float(v) for i, v in enumerate(pesos)},
        "dataset": {
            "sesiones": len(ds.session_start_idx),
            "sesiones_cortas_descartadas": ds.descartadas_cortas,
            "muestras": ds.sizes(),
            "ventana": WINDOW,
            "horizonte": HORIZON,
            "banda_stable_pct": ds.config.stable_band_pct,
            "distribucion": {s: ds.distribution(s) for s in ("train", "val", "test")},
            "fecha_min": min(s.window_end_ts for s in ds.train),
            "fecha_max": max(s.window_end_ts for s in ds.test),
        },
        "metricas": {
            split: {
                "mlp": Metricas.de(reales[split], pred_mlp[split][0]).como_dict(),
                "lineal": Metricas.de(reales[split], pred_lineal[split][0]).como_dict(),
            }
            for split in ("train", "val", "test")
        },
        "ic_95_test_mlp": list(intervalo_test),
        "baselines_test": {
            "mayoritario": {"clase": LABEL_NAMES[moda],
                            "macro_f1": macro_f1(y_test, mayoritario)},
            "azar_por_priors": {
                "macro_f1_medio": azar_media,
                "ic_95": list(azar_ic),
                # La misma tirada que entra en la comparación pareada, para que
                # `macro_f1_baseline` del bloque de comparaciones sea trazable.
                "macro_f1_tirada_representativa": macro_f1(y_test, azar_pred),
                "tiradas_promediadas": 1_000,
            },
            "persistencia": {"macro_f1": macro_f1(y_test, persistencia)},
            "lineal": {"macro_f1": macro_f1(y_test, pred_test_lineal)},
        },
        "comparaciones_test": comparaciones,
        # La calibración se valida PRIMERO en val y después se reporta test una
        # única vez, sin ningún bucle de ajuste contra test.
        "calibracion_val": calibracion(y_val, pred_val_mlp, conf_val),
        "calibracion_test": calibracion(y_test, pred_test_mlp, conf_test),
        "calibracion_por_clase_val": calibracion_por_clase(y_val, pred_val_mlp, conf_val),
        "calibracion_por_clase_test": calibracion_por_clase(y_test, pred_test_mlp, conf_test),
        "umbrales_val": tabla_umbrales(y_val, pred_val_mlp, conf_val),
        "umbrales_test": tabla_umbrales(y_test, pred_test_mlp, conf_test),
        "umbrales_definidos": UMBRALES_PERFIL,
        # Solo cuentan los casos en los que hay dirección que acertar: STABLE
        # queda fuera de los dos lados.
        "acierto_direccional_test": acierto_direccional(
            y_test, pred_test_mlp, sesiones_test,
            n_replicas=replicas, semilla=cfg.semilla,
        ),
        "cobertura_direccional_val": acierto_direccional(
            y_val, pred_val_mlp, sesiones_val,
            n_replicas=replicas, semilla=cfg.semilla,
        ),
        "tercios_test": metricas_por_tercios(y_test, pred_test_mlp),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cliente_influx():
    """Cliente de InfluxDB para el entrenamiento (solo corre en la Máquina 2).

    Se lee del entorno y **no tiene valor por defecto escrito en el código**:
    la convención 15.1 exige que las URLs de servicios vengan del entorno, para
    que en el despliegue solo haya que cambiar la configuración. En desarrollo
    esa URL vive en `.env`.

    El token es el administrador porque el reentrenamiento lee todo el
    histórico del bucket. Es correcto que solo viva en M2: la Máquina 1, que
    sí recibe un token de solo lectura y acotado al bucket, nunca entrena
    (§15.2).
    """
    from influxdb_client import InfluxDBClient
    url = os.environ.get("INFLUXDB_URL")
    if not url:
        raise SystemExit(
            "falta INFLUXDB_URL en el entorno. Cargar con:\n"
            "  set -a; source .env; source .env.local; set +a\n"
            "(en desarrollo la URL está en .env)"
        )
    token = os.environ.get("INFLUXDB_TOKEN") or os.environ.get("INFLUXDB_ADMIN_TOKEN")
    if not token:
        raise SystemExit(
            "falta INFLUXDB_TOKEN en el entorno. En desarrollo de M2 es "
            "INFLUXDB_ADMIN_TOKEN; en M1 se usa un token de solo lectura acotado "
            "al bucket, aunque M1 no entrena."
        )
    return InfluxDBClient(url=url, token=token,
                          org=os.environ.get("INFLUXDB_ORG", "ritech"))


def arquitectura_activa(valor: str | None = None) -> str:
    """Qué arquitectura queda como artefacto activo: `--arquitectura`, si no `MODEL_ARCH`.

    Cada corrida entrena **las dos** y las guarda ambas; esta función decide
    cuál puntea `active.json` y por lo tanto qué carga el servicio de M1.

    **La elección no se apoya en el rendimiento.** El lineal igualó o superó
    ligeramente al MLP en el Sprint 3 y la diferencia no alcanzó significancia
    estadística (informe §4). Se sirve el MLP porque el SRS §5.3 y el SDD §5.3.2
    exigen una red neuronal: la especificación manda, no una métrica que en
    este dataset no los separa. `MODEL_ARCH=linear` permite servir el otro
    sin tocar código.
    """
    candidato = (valor or os.environ.get("MODEL_ARCH") or "mlp").strip().lower()
    if candidato not in ARQUITECTURAS:
        raise SystemExit(
            f"arquitectura desconocida: {candidato!r}; usa {' o '.join(ARQUITECTURAS)}"
        )
    return candidato


def _parsear_argumentos(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m src.ai.train",
        description="Entrena el modelo predictivo local (RF-301/302) y guarda el artefacto.",
    )
    parser.add_argument("--arquitectura", choices=ARQUITECTURAS, default=None,
                        help=("qué arquitectura se deja activa; las dos se guardan. "
                              "Por defecto se lee MODEL_ARCH y, si no está, mlp"))
    parser.add_argument("--salida", default=str(MODELOS_DIR),
                        help="directorio de salida para artefacto e informe (por defecto: models/)")
    parser.add_argument("--inicio", default=None,
                        help="fecha inicial YYYY-MM-DD; por defecto, todo el histórico")
    parser.add_argument("--fin", default=None,
                        help="fecha final YYYY-MM-DD; por defecto, la última vela disponible")
    parser.add_argument("--semilla", type=int, default=SEMILLA_DEFECTO,
                        help=f"semilla de reproducibilidad (por defecto: {SEMILLA_DEFECTO})")
    parser.add_argument("--epocas", type=int, default=Entrenamiento.max_epochs,
                        help="épocas máximas; solo para experimentos, el plan fija 200")
    parser.add_argument("--paciencia", type=int, default=Entrenamiento.paciencia,
                        help="early stopping sobre val macro-F1; el plan fija 20")
    parser.add_argument("--dispositivo", default=None,
                        choices=("cpu", "cuda"),
                        help="por defecto se autodetecta (cuda en la Máquina 1)")
    parser.add_argument("--replicas", type=int, default=2000,
                        help="réplicas del bootstrap por sesión; el plan exige ≥1000")
    parser.add_argument("--solo-datos", action="store_true",
                        help="construye el dataset, lo describe y sale sin entrenar")
    return parser.parse_args(argv)


def _marcas_de_fecha(inicio: str | None, fin: str | None) -> tuple[int | None, int | None]:
    """Convierte fechas YYYY-MM-DD a epoch ms. Devuelve `(None, None)` si no se indican."""

    def a_ms(fecha: str) -> int:
        dt = datetime.strptime(fecha, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)

    return (a_ms(inicio) if inicio else None, a_ms(fin) if fin else None)


def _guardar(salida: Path, marca: str, activa: str,
             entrenadas: dict[str, tuple[nn.Module, dict[str, Any], int, float]],
             pesos: np.ndarray, ds: Dataset, dispositivo: str,
             informe: dict[str, Any]) -> tuple[dict[str, str], str]:
    """Escribe un artefacto por arquitectura y el puntero `active.json`.

    Devuelve `(rutas, ruta_informe)`.

    Se guarda **las dos** arquitecturas y `MODEL_ARCH` (o `--arquitectura`)
    decide cuál queda activa, así que cambiar de opinión es un ajuste de
    entorno y no un reentrenamiento.

    **La llave del informe no es la del artefacto**: `model_type` es `linear`
    porque es el identificador que recibe `make_model`, pero el informe la
    llama `lineal`. Esa diferencia ya causó un `KeyError` al salir del
    entrenamiento, después de haberlo pagado entero; `test_guardar_escribe_los_dos_artefactos`
    es la regresión.
    """
    rutas: dict[str, str] = {}
    ruta_informe = ""

    for tipo, (modelo, cfg_modelo, epocas, mejor_f1) in entrenadas.items():
        llave = "lineal" if tipo == "linear" else tipo
        nombre = f"ritech-{tipo}-{marca}"
        ruta_pt = salida / f"{nombre}.pt"
        ruta_json = salida / f"{nombre}.metrics.json"
        artifact = build_artifact(
            modelo, model_type=tipo, model_config=cfg_modelo,
            class_weights=pesos,
            stats_mean=ds.stats.mean, stats_std=ds.stats.std,
            stable_band_pct=ds.config.stable_band_pct,
            trained_at_ms=int(time.time() * 1000), training_device=dispositivo,
            metrics={
                "macro_f1_test": informe["metricas"]["test"][llave]["macro_f1"],
                "macro_f1_val": informe["metricas"]["val"][llave]["macro_f1"],
                "macro_f1_test_lineal": informe["baselines_test"]["lineal"]["macro_f1"],
                "macro_f1_test_persistencia": informe["baselines_test"]["persistencia"]["macro_f1"],
                "epocas_entrenadas": epocas,
                "mejor_val_macro_f1": mejor_f1,
                "duracion_segundos": informe.get("duracion_segundos"),
                # Se deja escrito para que nadie confunda la confianza con la
                # softmax cruda al leer el artefacto suelto.
                "confianza": "posterior corregido por prior",
            },
        )
        save_artifact(artifact, ruta_pt)
        ruta_json.write_text(json.dumps(informe, indent=2, ensure_ascii=False), encoding="utf-8")
        rutas[tipo] = str(ruta_pt)
        if tipo == activa:
            ruta_informe = str(ruta_json)

    # Puntero al artefacto activo: lo usan el servicio de inferencia de M1 y el
    # timer de reentrenamiento. Archivo de texto, no enlace simbólico, para no
    # acoplar el código a las convenciones de un SO concreto (convención 5.4).
    #
    # Las rutas se guardan **relativas al propio puntero** y no a la raíz del
    # proyecto. La diferencia es `models/x.pt` contra `x.pt`: con la primera,
    # quien lee el puntero (resolviendo contra su directorio) construye
    # `models/models/x.pt` y el servicio de M1 no arranca. Ver el detalle y el
    # porqué en `src.ai.model.ruta_desde_puntero`, que es la única regla.
    # `relative_to` revienta aquí si alguna vez los artefactos dejan de
    # escribirse en `salida`, que es preferible a guardar una ruta que nadie
    # podrá resolver después de pagar entero el entrenamiento.
    relativas = {tipo: Path(ruta).relative_to(salida) for tipo, ruta in rutas.items()}

    (salida / "active.json").write_text(
        json.dumps(
            {
                "path": str(relativas[activa]),
                "arquitectura": activa,
                "entrenado_en": marca,
                "esquema": FEATURE_SCHEMA_VERSION,
                "confianza": "posterior corregido por prior",
                "disponibles": {tipo: str(ruta) for tipo, ruta in relativas.items()},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return rutas, ruta_informe


def main(argv: list[str] | None = None) -> int:
    args = _parsear_argumentos(argv)

    if args.replicas < 1000:
        raise SystemExit(
            f"el plan §4 exige al menos 1.000 réplicas de bootstrap, no {args.replicas}"
        )

    dispositivo = args.dispositivo or ("cuda" if torch.cuda.is_available() else "cpu")
    cfg = Entrenamiento(
        semilla=args.semilla, max_epochs=args.epocas, paciencia=args.paciencia
    )

    inicio_ms, fin_ms = _marcas_de_fecha(args.inicio, args.fin)
    cliente = _cliente_influx()
    try:
        print("Cargando velas desde InfluxDB…", file=sys.stderr)
        velas = load_candles(cliente, start_ts=inicio_ms, end_ts=fin_ms)
    finally:
        cliente.close()
    if not velas:
        raise SystemExit("no se obtuvo ninguna vela: revisar bucket, org y rango de fechas")
    print(f"  {len(velas):,} velas", file=sys.stderr)

    t0 = time.perf_counter()
    ds = build_dataset(velas, DatasetConfig())
    print(f"  {len(ds.train):,} train / {len(ds.val):,} val / {len(ds.test):,} test "
          f"({ds.descartadas_cortas} sesiones cortas descartadas)", file=sys.stderr)
    if not (ds.train and ds.val and ds.test):
        raise SystemExit("un corte quedó vacío: hay muy pocas sesiones para entrenar")

    if args.solo_datos:
        print(json.dumps({
            "sesiones": len(ds.session_start_idx),
            "sesiones_cortas": ds.descartadas_cortas,
            "muestras": ds.sizes(),
            "distribucion": {s: ds.distribution(s) for s in ("train", "val", "test")},
            "banda_stable_pct": ds.config.stable_band_pct,
        }, indent=2, ensure_ascii=False))
        return 0

    y_train = np.asarray([s.y for s in ds.train], dtype=np.int64)
    pesos = pesos_de_clase(y_train)
    print(f"  pesos de clase (solo train): {pesos.round(3).tolist()}", file=sys.stderr)

    print(f"Entrenando baseline lineal ({dispositivo})…", file=sys.stderr)
    modelo_lineal, hist_lineal, f1_lineal = entrenar_lineal(ds, cfg, pesos, dispositivo)

    print(f"Entrenando MLP ({dispositivo})…", file=sys.stderr)
    modelo_mlp, hist_mlp, f1_mlp = entrenar_mlp(ds, cfg, pesos, dispositivo)

    # Se reporta en qué época paró el early stopping: si parara en la primera
    # sería señal de que el modelo no está aprendiendo nada de los datos.
    print(f"  lineal: {len(hist_lineal)} épocas, mejor val macro-F1 {f1_lineal:.4f}",
          file=sys.stderr)
    print(f"  mlp:    {len(hist_mlp)} épocas, mejor val macro-F1 {f1_mlp:.4f}",
          file=sys.stderr)

    print("Calculando baselines, bootstrap, calibración y umbrales…", file=sys.stderr)
    informe = construir_informe(ds, modelo_mlp, modelo_lineal, cfg, pesos, dispositivo,
                                replicas=args.replicas)
    informe["duracion_segundos"] = round(time.perf_counter() - t0, 1)
    # Dónde paró cada uno: si el early stopping se activara en la primera época,
    # el modelo no está aprendiendo nada de los datos y el informe debe verse.
    informe["modelo"]["epocas_entrenadas"] = {
        "mlp": len(hist_mlp), "lineal": len(hist_lineal),
    }
    informe["modelo"]["mejor_val_macro_f1"] = {
        "mlp": f1_mlp, "lineal": f1_lineal,
    }

    # --- guardado versionado ---
    marca = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    salida = Path(args.salida)
    salida.mkdir(parents=True, exist_ok=True)
    activa = arquitectura_activa(args.arquitectura)
    entrenadas: dict[str, tuple[nn.Module, dict[str, Any], int, float]] = {
        "mlp": (modelo_mlp, dict(DEFAULT_MODEL_CONFIG), len(hist_mlp), f1_mlp),
        "linear": (modelo_lineal, dict(DEFAULT_LINEAR_CONFIG), len(hist_lineal), f1_lineal),
    }
    rutas, ruta_informe = _guardar(salida, marca, activa, entrenadas,
                                   pesos, ds, dispositivo, informe)

    _imprimir_resumen(informe)
    print(f"\nArtefacto activo ({activa}): {rutas[activa]}", file=sys.stderr)
    print(f"Informe:                  {ruta_informe}", file=sys.stderr)
    return 0


def _imprimir_resumen(informe: dict) -> None:
    mlp = informe["metricas"]["test"]["mlp"]
    lin = informe["metricas"]["test"]["lineal"]
    print("\n=== RESULTADOS EN TEST (evaluado UNA vez) ===")
    print(f"  MLP    macro-F1 {mlp['macro_f1']:.4f}  accuracy {mlp['accuracy']:.4f}")
    print(f"  Lineal macro-F1 {lin['macro_f1']:.4f}  accuracy {lin['accuracy']:.4f}")
    print(f"  IC 95% del MLP: [{informe['ic_95_test_mlp'][0]:.4f}, "
          f"{informe['ic_95_test_mlp'][1]:.4f}]  (bootstrap por sesión)")
    print("\n  Baselines:")
    for nombre, datos in informe["baselines_test"].items():
        valor = datos.get("macro_f1", datos.get("macro_f1_medio"))
        print(f"    {nombre:<18} macro-F1 {valor:.4f}")
    print("\n  ¿Ventaja demostrada? (IC de la diferencia no incluye el 0)")
    for nombre, datos in informe["comparaciones_test"].items():
        ic = datos["ic_95_diferencia"]
        marca = "SÍ" if datos["ventaja_demostrada"] else "NO"
        print(f"    vs {nombre:<18} Δ={datos['diferencia']:+.4f}  "
              f"IC [{ic[0]:+.4f}, {ic[1]:+.4f}]  {marca}")
    print("\n  Umbrales de perfil de riesgo sobre test (confianza corregida por prior):")
    for fila in informe["umbrales_test"]:
        pu = f"{fila['precision_up']:.3f}" if fila["precision_up"] is not None else "—"
        pd_ = f"{fila['precision_down']:.3f}" if fila["precision_down"] is not None else "—"
        print(f"    ≥{fila['umbral']:.2f}: {fila['n_predicciones']:>5} predicciones "
              f"({fila['pct_del_total']:5.1f}%)  precisión UP {pu} / DOWN {pd_}")

    dir_ = informe["acierto_direccional_test"]
    ic = dir_["ic_95"]
    ic_txt = f"[{ic[0]:.3f}, {ic[1]:.3f}]" if ic else "—"
    print("\n  Acierto direccional (solo casos donde verdad Y predicción son UP/DOWN):")
    print(f"    {dir_['aciertos']}/{dir_['n']} = {dir_['ratio']:.3f}  IC 95% {ic_txt}")
    print(f"    (de {dir_['casos_direccionales_verdad']} muestras con verdad direccional)")


if __name__ == "__main__":
    raise SystemExit(main())
