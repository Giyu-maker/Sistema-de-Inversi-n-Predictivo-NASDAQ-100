"""Modelo predictivo local — SDD §5.3. **Este módulo sí importa `torch`.**

Corre en la **Máquina 1** (inferencia con GPU) y en la Máquina 2 solo para
pruebas (grupo `ai`). Las firmas de `LocalPredictiveModel` son **literales a
SDD §5.3.2**: `__init__(model_path)`, `load_model(path)` y
`run_inference(feature_tensor) -> (trend, confidence, rationale)`.

Contrato de inferencia
----------------------
`run_inference` recibe la ventana de features **sin normalizar**, tal y como la
produce `extract_window()`, con forma ``(WINDOW, N_FEATURES)``. La normalización
se hace aquí con la media/desviación que viajan **dentro del artefacto**.

El motivo es que la normalización depende del train y, si el que llama tuviera
que aplicarla, habría dos lugares donde puede salir mal y uno de ellos estaría
en M3/M1 sin posibilidad de verificarlo. Los estadísticos viajan congelados con
los pesos: no hay forma de que el modelo se sirva con una normalización que no
sea la con la que se entrenó.

Artefacto portátil
------------------
Se guarda el ``state_dict`` y se carga con ``map_location``, así que un modelo
entrenado en CPU (M2, aquí) carga en la GPU de M1 sin reconstruirlo, y a la
inversa.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch import nn

from src.ai.features import (
    FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION,
    HORIZON,
    LABEL_NAMES,
    WINDOW,
)

#: Versión del contenedor del artefacto (formato del archivo, no de las features).
ARTIFACT_FORMAT_VERSION = 1

DEFAULT_MODEL_CONFIG: dict[str, int | float] = {
    "input_dim": WINDOW * len(FEATURE_NAMES),  # 120
    "hidden1": 64,
    "hidden2": 32,
    "n_classes": len(LABEL_NAMES),
    "dropout": 0.20,
}


class SchemaVersionError(RuntimeError):
    """El artefacto se entrenó con un esquema de features distinto al de este servicio."""


def _plain(obj: Any) -> Any:
    """Convierte tipos de numpy a tipos básicos de Python.

    `torch.load(weights_only=True)` solo admite tipos básicos y tensores; un
    `np.float64` dentro de los metrics rompería la carga en M1. Además deja el
    artefacto serializable sin depender de numpy en el lado que lo lee.
    """
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


class NasdaqMLP(nn.Module):
    """MLP 120 → 64 → 32 → 3 con ReLU y dropout (plan §2, congelado)."""

    def __init__(
        self,
        input_dim: int = int(DEFAULT_MODEL_CONFIG["input_dim"]),
        hidden1: int = 64,
        hidden2: int = 32,
        n_classes: int = 3,
        dropout: float = 0.20,
    ) -> None:
        super().__init__()
        if dropout < 0 or dropout >= 1:
            raise ValueError(f"dropout debe estar en [0, 1), no {dropout}")
        self.net = nn.Sequential(
            nn.Linear(input_dim, hidden1),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden1, hidden2),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden2, n_classes),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


class NasdaqLinear(nn.Module):
    """Baseline lineal (plan §3, baseline 4): una sola capa ``Linear(120 → 3)``.

    Existe para responder una sola pregunta: **¿el MLP justifica su
    complejidad?** Se entrena con el mismo procedimiento que el MLP. Si no lo
    supera, se reporta tal cual y se propone quedarse con este.
    """

    def __init__(self, input_dim: int = int(DEFAULT_MODEL_CONFIG["input_dim"]),
                 n_classes: int = 3) -> None:
        super().__init__()
        self.fc = nn.Linear(input_dim, n_classes)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.fc(x)


# ---------------------------------------------------------------------------
# Artefacto
# ---------------------------------------------------------------------------

def build_artifact(
    model: nn.Module,
    *,
    model_type: str,
    model_config: dict[str, Any],
    stats_mean: np.ndarray,
    stats_std: np.ndarray,
    stable_band_pct: float,
    trained_at_ms: int,
    training_device: str,
    metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Empaqueta pesos + estadísticos de normalización + esquema en un único dict.

    Todo lo que necesita inferencia viaja aquí. **Ningún estadístico se
    recalcula al servir**: media y desviación son las del train y punto.
    """
    if stats_mean.shape != stats_std.shape:
        raise ValueError("stats_mean y stats_std deben tener la misma forma")
    if stats_mean.shape != (len(FEATURE_NAMES),):
        raise ValueError(
            f"los estadísticos deben tener forma ({len(FEATURE_NAMES)},), no {stats_mean.shape}"
        )
    if not np.isfinite(stats_mean).all() or not np.isfinite(stats_std).all():
        raise ValueError("los estadísticos de normalización contienen NaN o infinito")
    if not (stats_std > 0).all():
        raise ValueError("la desviación de normalización tiene ceros: habría división por cero")
    if model_type not in ("mlp", "linear"):
        raise ValueError(f"model_type desconocido: {model_type!r}")

    return {
        "artifact_format": ARTIFACT_FORMAT_VERSION,
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "model_type": model_type,
        "model_config": _plain(model_config),
        "state_dict": model.state_dict(),
        "feature_names": list(FEATURE_NAMES),
        "window": WINDOW,
        "horizon": HORIZON,
        "stable_band_pct": float(stable_band_pct),
        "label_names": {int(k): v for k, v in LABEL_NAMES.items()},
        "stats_mean": [float(v) for v in stats_mean],
        "stats_std": [float(v) for v in stats_std],
        "trained_at_ms": int(trained_at_ms),
        "training_device": training_device,
        # `str()` deliberado: `torch.__version__` es un objeto TorchVersion, y un
        # objeto propio no sobrevive a `weights_only=True` al cargar. Como
        # cadena, el artefacto solo contiene tipos básicos y tensores.
        "torch_version": str(torch.__version__),
        "metrics": _plain(metrics or {}),
    }


def save_artifact(artifact: dict[str, Any], path: str | Path) -> Path:
    """Escribe el artefacto creando los directorios que hagan falta."""
    destino = Path(path)
    destino.parent.mkdir(parents=True, exist_ok=True)
    torch.save(artifact, destino)
    return destino


def load_artifact(path: str | Path, *, map_location: str | torch.device = "cpu") -> dict[str, Any]:
    """Lee el artefacto.

    ``weights_only=True``: el archivo se interpreta como contenedor de tensores
    y tipos básicos, nunca como programa. Si alguien sustituye el `.pt` por un
    pickle malicioso, la carga falla en lugar de ejecutarlo.
    """
    origen = Path(path)
    if not origen.is_file():
        raise FileNotFoundError(f"no existe el artefacto del modelo: {origen}")
    # `weights_only` está disponible desde torch 2.0; el pin del proyecto es 2.9.
    return torch.load(origen, map_location=map_location, weights_only=True)  # type: ignore[call-arg]


def check_schema_version(artifact: dict[str, Any]) -> None:
    """Rechaza un artefacto entrenado con otro esquema de features.

    Un modelo entrenado con features distintas a las que le sirven no predice:
    devuelve números con forma de predicción. Esto es la defensa de la línea de
    contrato `feature_schema_version` entre M2 y M1.
    """
    version = str(artifact.get("feature_schema_version", ""))
    if version != FEATURE_SCHEMA_VERSION:
        raise SchemaVersionError(
            f"esquema de features incompatible: el artefacto declara {version!r} y "
            f"este servicio espera {FEATURE_SCHEMA_VERSION!r}. Reentrenar con el "
            f"esquema actual antes de servir."
        )


def make_model(model_type: str, model_config: dict[str, Any]) -> nn.Module:
    """Reconstruye el módulo a partir de la configuración guardada en el artefacto."""
    if model_type == "mlp":
        return NasdaqMLP(**model_config)
    if model_type == "linear":
        return NasdaqLinear(**model_config)
    raise ValueError(f"model_type desconocido en el artefacto: {model_type!r}")


# ---------------------------------------------------------------------------
# Clase de inferencia — firmas literales del SDD §5.3.2
# ---------------------------------------------------------------------------

class LocalPredictiveModel:
    """SDD §5.3 — carga el modelo local y ejecuta inferencia con XAI y confianza."""

    def __init__(self, model_path: str):
        # Selección de dispositivo literal al SDD; en M1 hay CUDA y en M2 no.
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        # Los bytes se leen UNA vez aquí; load_model construye el módulo a partir
        # de este dict. Se conserva el parámetro `path` por firma del SDD.
        self.artifact = load_artifact(model_path, map_location=self.device)
        check_schema_version(self.artifact)
        self.model = self.load_model(model_path)

        # Metadatos que run_inference necesita para normalizar y explicar.
        self.feature_names: tuple[str, ...] = tuple(self.artifact["feature_names"])
        self.window: int = int(self.artifact["window"])
        self.stable_band_pct: float = float(self.artifact["stable_band_pct"])
        self.stats_mean = np.asarray(self.artifact["stats_mean"], dtype=np.float32)
        self.stats_std = np.asarray(self.artifact["stats_std"], dtype=np.float32)
        self.label_names: dict[int, str] = {int(k): v for k, v in self.artifact["label_names"].items()}

    def load_model(self, path: str) -> nn.Module:
        """Carga los pesos de la red neuronal directamente en la GPU local.

        Usa `map_location=self.device`, de modo que un artefacto entrenado en
        CPU se carga en la GPU de M1 sin reconstruirlo ni volver a entrenar.
        """
        del path  # ya leído en __init__; se conserva por la firma del SDD
        modelo = make_model(str(self.artifact["model_type"]), dict(self.artifact["model_config"]))
        modelo.load_state_dict(self.artifact["state_dict"])
        modelo.to(self.device)
        modelo.eval()  # dropout apagado: la inferencia debe ser determinista
        return modelo

    # -- normalización interna ------------------------------------------------

    def _prepare(self, feature_tensor: torch.Tensor | np.ndarray) -> torch.Tensor:
        """Valida la ventana, normaliza con los estadísticos del artefacto y la lleva al device.

        Acepta ``(WINDOW, N_FEATURES)``, un lote ``(N, WINDOW, N_FEATURES)`` o el
        vector aplanado; lo único que exige es que el número total de features
        por ventana sea el del esquema con el que se entrenó.
        """
        x = np.asarray(feature_tensor, dtype=np.float32)
        if x.ndim == 1:
            if x.size % len(self.feature_names) != 0:
                raise ValueError(
                    f"el vector aplanado ({x.size}) no es múltiplo de "
                    f"{len(self.feature_names)} features"
                )
            x = x.reshape(-1, len(self.feature_names))
        if x.ndim == 2:
            # Una sola ventana: (WINDOW, N_FEATURES) → añadir dimensión de lote.
            if x.shape[1] != len(self.feature_names):
                raise ValueError(
                    f"se esperaban {len(self.feature_names)} features por vela, "
                    f"la ventana tiene {x.shape[1]}"
                )
            x = x[None, ...]
        elif x.ndim != 3:
            raise ValueError(f"forma de features no soportada: {x.shape}")

        if x.shape[1] != self.window:
            raise ValueError(
                f"la ventana debe tener {self.window} velas, no {x.shape[1]}"
            )
        if x.shape[2] != len(self.feature_names):
            raise ValueError(
                f"el artefacto espera {len(self.feature_names)} features, "
                f"la entrada tiene {x.shape[2]}"
            )

        # Normalización con los estadísticos congelados del artefacto, y
        # aplanado a (N, WINDOW·N_FEATURES): la red recibe un vector plano.
        normalizado = (x - self.stats_mean) / self.stats_std
        aplanado = normalizado.reshape(normalizado.shape[0], -1)
        tensor = torch.from_numpy(np.ascontiguousarray(aplanado, dtype=np.float32))
        return tensor.to(self.device)

    def run_inference(self, feature_tensor: torch.Tensor) -> tuple[str, float, str]:
        """
        Ejecuta la predicción y retorna:
        (trend_prediction: 'UP'|'DOWN'|'STABLE', confidence_score, rationale_text)

        La confianza (RF-304) es la probabilidad softmax de la clase ganadora.
        **Este método no decide nada sobre el umbral**: el corte al 75 % y los
        perfiles de riesgo son responsabilidad del `RiskManager` en el Sprint 4.
        """
        from src.ai.xai import build_rationale, gradient_x_input

        x = self._prepare(feature_tensor)
        with torch.no_grad():
            logits = self.model(x)
            probs = torch.softmax(logits, dim=-1)
        indice = int(probs[0].argmax().item())
        confianza = float(probs[0, indice].item())

        atribucion = gradient_x_input(self.model, x, indice)[0]
        rationale = build_rationale(
            atribucion=atribucion,
            probabilities=probs[0].detach().cpu().numpy(),
            predicted_index=indice,
            feature_names=self.feature_names,
            label_names=self.label_names,
        )
        return self.label_names[indice], confianza, rationale

    def predict_proba(self, feature_tensor: torch.Tensor | np.ndarray) -> np.ndarray:
        """Probabilidad de las tres clases, en orden DOWN/STABLE/UP.

        No está en el SDD, pero la calibración (plan §5) y el `RiskManager` del
        Sprint 4 necesitan ver las tres, no solo la ganadora.
        """
        x = self._prepare(feature_tensor)
        with torch.no_grad():
            return torch.softmax(self.model(x), dim=-1).cpu().numpy()
