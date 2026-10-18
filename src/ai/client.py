"""Cliente de inferencia — vive en la Máquina 2 y habla con la Máquina 1 (§15).

Transporta lo que RF-303 (razón técnica) y RF-304 (ratio de confianza) exigen
de cada predicción, desde la máquina que la produce hasta la que la persiste.
**Este módulo no importa `torch`.** Ese es el motivo por el que existe como
archivo aparte y no dentro de `model.py`: M2 orquesta el sistema completo
(ingesta, riesgo, auditoría, API del dashboard) y no debería tener que
instalar el grupo `ai` para pedirle una predicción a M1. Aquí solo hay
`httpx`, que ya está en el grupo base.

Qué protege
-----------
1. **La URL viene del entorno**, nunca escrita en el código (§15.1). En
   desarrollo apunta a `localhost`; en despliegue, a la IP interna de M1.
2. **La forma de la ventana se valida aquí antes de enviarla.** Un tensor mal
   formado viaja igual, pero el error se produce en la máquina que lo tiene
   la culpa y con un mensaje útil.
3. **La versión del esquema se compara al recibir.** La forma del tensor es
   la misma (120) aunque el significado de las columnas haya cambiado; sin
   esta comprobación el sistema seguiría "funcionando" y prediciendo sobre
   features que ya no significan lo mismo. `/health` lo permite comprobar
   antes de la primera predicción.

Confianza
---------
`Prediccion.confidence_score` es el **posterior corregido por prior**, el
mismo que define `src.ai.metrics.corregir_posterior` y el que se expone en el
modelo local. No es la softmax cruda de la red. El `RiskManager` del Sprint 4
debe tratarla como la probabilidad de que la clase elegida sea la correcta.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Sequence

import httpx
import numpy as np

from src.ai.features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION, LABEL_NAMES, WINDOW

#: Orden de las clases en `posterior_corregido`.
ORDEN_CLASES = tuple(LABEL_NAMES.values())


class ErrorDeInferencia(RuntimeError):
    """El servicio de M1 no respondió o respondió algo que no se puede usar."""


class ErrorDeEsquema(ErrorDeInferencia):
    """M1 sirve un modelo entrenado con un esquema de features distinto al nuestro."""


@dataclass(frozen=True)
class Prediccion:
    """Lo que M2 recibe y lo que el resto del sistema consume.

    `trend_prediction` usa los literales de RF-304 (`UP`|`DOWN`|`STABLE`).
    """

    trend_prediction: str
    #: **Posterior corregido por prior**, no la softmax cruda.
    confidence_score: float
    technical_rationale: str
    posterior_corregido: dict[str, float]
    feature_schema_version: str
    model_type: str
    stable_band_pct: float
    generado_en_ms: int
    latencia_ms: int


class InferenceClient:
    """Cliente HTTP del servicio de inferencia de la Máquina 1.

    Sin estado entre llamadas y sin dependencia de `torch`: se puede instanciar
    en cualquier proceso de M2, incluido el del `KillSwitchEngine`, que no
    debería cargar un framework de deep learning para pedir una predicción.
    """

    def __init__(self, base_url: str | None = None, *, timeout_s: float = 5.0) -> None:
        url = base_url or os.environ.get("INFERENCE_URL")
        if not url:
            raise ErrorDeInferencia(
                "falta INFERENCE_URL en el entorno. Debe apuntar al servicio de "
                "inferencia de la Máquina 1 (en desarrollo, http://127.0.0.1:8000)."
            )
        self._url = url.rstrip("/")
        self._timeout_s = timeout_s

    @property
    def url(self) -> str:
        return self._url

    # -- operaciones ---------------------------------------------------------

    def salud(self) -> dict[str, Any]:
        """`GET /health`. Devuelve el cuerpo tal cual, para inspección."""
        return self._get("/health")

    def verificar_esquema(self) -> str:
        """Comprueba que M1 sirve un modelo entrenado con nuestro mismo esquema.

        Devuelve la versión que declara M1. **Levanta `ErrorDeEsquema` si no
        coincide con la local**, porque seguir igualaría a interpretar 120
        números que ya no significan lo mismo.
        """
        remoto = str(self.salud().get("feature_schema_version", ""))
        if remoto != FEATURE_SCHEMA_VERSION:
            raise ErrorDeEsquema(
                f"esquema de features incompatible: M2 usa {FEATURE_SCHEMA_VERSION} y "
                f"M1 sirve {remoto or '(sin declarar)'}. Reentrenar en M1 con el "
                f"esquema actual antes de continuar."
            )
        return remoto

    def predecir(self, features: Sequence[Sequence[float]] | np.ndarray) -> Prediccion:
        """Predice sobre una ventana `(WINDOW, N_FEATURES)` **sin normalizar**.

        La normalización la hace el modelo de M1 con los estadísticos
        congelados en el artefacto; enviar aquí una ventana ya normalizada
        sería normalizar dos veces y nadie lo notaría.
        """
        ventana = self._validar_ventana(features)
        inicio = time.perf_counter()
        cuerpo = self._post("/predict", {"features": ventana.tolist()})
        latencia = int((time.perf_counter() - inicio) * 1000)
        return self._interpretar_prediccion(cuerpo, latencia)

    def _interpretar_prediccion(self, cuerpo: dict[str, Any], latencia: int) -> Prediccion:
        """Comprueba el contrato antes de que nadie decida nada con esa respuesta.

        El `RiskManager` del Sprint 4 va a tomar decisiones con estos números.
        Un `trend_prediction` fuera de los tres literales de RF-304, o una
        confianza de 1,7, tienen que morir aquí y no llegando a la lógica de
        trading.
        """
        try:
            tendencia = str(cuerpo["trend_prediction"])
            confianza = float(cuerpo["confidence_score"])
            posterior = {str(k): float(v)
                         for k, v in cuerpo["posterior_corregido"].items()}
            esquema = str(cuerpo["feature_schema_version"])
            pred = Prediccion(
                trend_prediction=tendencia,
                confidence_score=confianza,
                technical_rationale=str(cuerpo["technical_rationale"]),
                posterior_corregido=posterior,
                feature_schema_version=esquema,
                model_type=str(cuerpo["model_type"]),
                stable_band_pct=float(cuerpo["stable_band_pct"]),
                generado_en_ms=int(cuerpo["generado_en_ms"]),
                latencia_ms=latencia,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ErrorDeInferencia(
                f"la respuesta de {self._url}/predict no cumple el contrato: {exc}"
            ) from exc

        if pred.trend_prediction not in ORDEN_CLASES:
            raise ErrorDeInferencia(
                f"trend_prediction fuera de {ORDEN_CLASES}: {pred.trend_prediction!r}"
            )
        if not 0.0 <= pred.confidence_score <= 1.0:
            raise ErrorDeInferencia(
                f"confidence_score fuera de [0, 1]: {pred.confidence_score}"
            )
        if set(pred.posterior_corregido) != set(ORDEN_CLASES):
            raise ErrorDeInferencia(
                f"posterior_corregido debe traer las {len(ORDEN_CLASES)} clases, "
                f"trajo {sorted(pred.posterior_corregido)}"
            )
        # Comprobación anti-desajuste silencioso: la forma del tensor es la
        # misma aunque el significado de las columnas haya cambiado.
        if pred.feature_schema_version != FEATURE_SCHEMA_VERSION:
            raise ErrorDeEsquema(
                f"M1 sirve un modelo entrenado con el esquema {pred.feature_schema_version} "
                f"y nosotros hablamos el {FEATURE_SCHEMA_VERSION}. Reentrenar en M1 antes "
                f"de continuar."
            )
        return pred

    # -- validación local ----------------------------------------------------

    @staticmethod
    def _validar_ventana(features: Sequence[Sequence[float]] | np.ndarray) -> np.ndarray:
        ventana = np.asarray(features, dtype=np.float32)
        if ventana.shape != (WINDOW, len(FEATURE_NAMES)):
            raise ValueError(
                f"se espera una ventana {WINDOW}×{len(FEATURE_NAMES)} "
                f"(velas × features), se obtuvo {ventana.shape}"
            )
        if not np.isfinite(ventana).all():
            raise ValueError("la ventana contiene NaN o infinito")
        return ventana

    # -- transporte ----------------------------------------------------------

    def _get(self, ruta: str) -> dict[str, Any]:
        try:
            with httpx.Client(base_url=self._url, timeout=self._timeout_s) as cliente:
                respuesta = cliente.get(ruta)
        except httpx.HTTPError as exc:
            raise ErrorDeInferencia(f"sin respuesta de {self._url}{ruta}: {exc}") from exc
        return self._interpretar(respuesta, ruta)

    def _post(self, ruta: str, cuerpo: dict[str, Any]) -> dict[str, Any]:
        try:
            with httpx.Client(base_url=self._url, timeout=self._timeout_s) as cliente:
                respuesta = cliente.post(ruta, json=cuerpo)
        except httpx.HTTPError as exc:
            raise ErrorDeInferencia(f"sin respuesta de {self._url}{ruta}: {exc}") from exc
        return self._interpretar(respuesta, ruta)

    def _interpretar(self, respuesta: httpx.Response, ruta: str) -> dict[str, Any]:
        if respuesta.status_code >= 400:
            raise ErrorDeInferencia(
                f"{self._url}{ruta} devolvió {respuesta.status_code}: "
                f"{respuesta.text[:300]}"
            )
        try:
            datos = respuesta.json()
        except ValueError as exc:
            raise ErrorDeInferencia(
                f"{self._url}{ruta} devolvió algo que no es JSON"
            ) from exc
        if not isinstance(datos, dict):
            raise ErrorDeInferencia(
                f"{self._url}{ruta} devolvió {type(datos).__name__}, se esperaba un objeto"
            )
        return datos


def cliente_desde_entorno() -> InferenceClient:
    """Constructor por defecto: URL y timeout salen del entorno (§15.1)."""
    bruto = os.environ.get("INFERENCE_TIMEOUT_S", "5")
    try:
        timeout = float(bruto)
    except ValueError as exc:
        raise ErrorDeInferencia(f"INFERENCE_TIMEOUT_S no es un número: {bruto!r}") from exc
    return InferenceClient(timeout_s=timeout)
