"""Servicio de inferencia — la frontera entre la Máquina 2 y la Máquina 1 (§15).

Entrega lo que RF-303 (XAI: la razón técnica de cada sugerencia) y RF-304 (el
ratio de confianza) exigen adjuntar a cada predicción; que corra en M1 y no en
M2 es una decisión de arquitectura (`AGENTS.md` §15), no un requisito del SRS.
Este es el **único** proceso que corre en la Máquina 1 y el único punto por el
que el resto del sistema le pide una predicción. Es deliberadamente pequeño:
`/health` y `/predict`, nada más.

Qué expone y por qué
--------------------
`/predict` recibe la ventana de features **sin normalizar**, tal y como la
produce `extract_window()`, y devuelve `(trend, confidence, rationale)` con la
confianza **corregida por prior** (`src.ai.metrics.corregir_posterior`), no la
softmax cruda. La normalización ocurre dentro del modelo con los estadísticos
que viajan congelados en el artefacto: si el que llama tuviera que aplicarla,
habría dos lugares donde puede salir mal y uno de ellos estaría en una máquina
donde nadie puede verificarlo.

La respuesta **declara `feature_schema_version`**. El cliente de M2 la compara
con la suya y se niega a seguir si no coinciden: la forma del tensor es la
misma (120) aunque el significado de las columnas haya cambiado, y esa clase
de fallo es silenciosa y cara. `LocalPredictiveModel` ya la valida al cargar;
la duplicamos en la respuesta para que el que llama pueda comprobarlo sin
tener que confiar en que el servicio está vivo y actualizado.

Qué NO hace
-----------
- **No aplica ningún umbral.** El corte del 75 % y los perfiles de riesgo son
  responsabilidad del `RiskManager` (Sprint 4). El modelo informa, no decide.
- **No escribe en PostgreSQL.** Las predicciones las persiste M2 en
  `ai_predictions`; M1 no tiene credenciales de la base (convención §15.2).
- **No entrena.** El reentrenamiento es el CLI `src.ai.train`, otro proceso.

Corre con el grupo `ai` (torch). El cliente de M2, en cambio, no lo necesita.
"""

from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.ai.features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION, LABEL_NAMES, WINDOW
from src.ai.model import (
    ARTIFACT_FORMAT_VERSION,
    LocalPredictiveModel,
    ruta_desde_puntero,
)

#: Ventana de features que admite el servicio. Es la misma constante que usa el
#: entrenamiento; se redeclara aquí solo para el mensaje de error.
ESPERA_FEATURES = (WINDOW, len(FEATURE_NAMES))


# ---------------------------------------------------------------------------
# Contrato de entrada y salida — Pydantic estricto (RNF-011)
# ---------------------------------------------------------------------------

class VentanaFeatures(BaseModel):
    """La ventana `(WINDOW, N_FEATURES)` sin normalizar.

    `extra="forbid"` y validación de forma y de valores finitos: una ventana
    con una columna de más, o con un `NaN` dentro, produce una predicción
    absurda en lugar de un error, y eso es exactamente el tipo de fallo que
    no se ve hasta que ya costó dinero.
    """

    model_config = ConfigDict(extra="forbid")

    features: list[list[float]]

    @field_validator("features")
    @classmethod
    def _validar_forma(cls, valor: list[list[float]]) -> list[list[float]]:
        filas, cols = len(valor), (len(valor[0]) if valor else 0)
        if (filas, cols) != ESPERA_FEATURES:
            raise ValueError(
                f"se espera una ventana {ESPERA_FEATURES[0]}×{ESPERA_FEATURES[1]} "
                f"(velas × features), se recibió {filas}×{cols}"
            )
        plano = [v for fila in valor for v in fila]
        if not all(np.isfinite(plano)):
            raise ValueError("la ventana contiene NaN o infinito")
        return valor


class PrediccionSalida(BaseModel):
    """Respuesta de `/predict`.

    Los nombres `trend_prediction`, `confidence_score` y
    `technical_rationale` son los de RF-304 y del SDD §5.3.2; se mantienen
    literales para que la frontera sea reconocible al revés del contrato.
    """

    model_config = ConfigDict(extra="forbid")

    trend_prediction: Literal["UP", "DOWN", "STABLE"]
    #: **Posterior corregido por prior**, no la softmax cruda. Ver módulo.
    confidence_score: float = Field(ge=0.0, le=1.0)
    technical_rationale: str
    #: Las tres clases del posterior corregido, para el RiskManager del Sprint 4.
    posterior_corregido: dict[str, float]
    #: Contrato anti-desajuste silencioso entre M2 y M1.
    feature_schema_version: str
    artifact_format_version: int
    model_type: Literal["mlp", "linear"]
    stable_band_pct: float
    generado_en_ms: int


class EstadoSalida(BaseModel):
    """Respuesta de `/health`."""

    model_config = ConfigDict(extra="forbid")

    estado: Literal["ok"]
    modelo_cargado: bool
    model_type: Literal["mlp", "linear"]
    feature_schema_version: str
    artifact_format_version: int
    ventana: int
    n_features: int
    dispositivo: str
    artefacto: str
    subiendo_desde_ms: int


# ---------------------------------------------------------------------------
# Resolución del artefacto — sin rutas escritas en el código (§15.1)
# ---------------------------------------------------------------------------

def ruta_del_artefacto() -> Path:
    """De dónde sale el modelo que sirve este proceso.

    Todo viene del entorno, para que en el despliegue solo haya que cambiar la
    configuración y no el código (§15.1).

    `MODEL_PATH` puede apuntar **a un archivo `.pt`** o **a un directorio**. En
    el segundo caso se lee `active.json` dentro y se usa su `path`, que es lo
    que escribe `src.ai.train` tras cada reentrenamiento: así el timer diario
    puede dejar un modelo nuevo y reiniciar el servicio sin reconfigurar nada.
    """
    bruto = os.environ.get("MODEL_PATH")
    if not bruto:
        raise RuntimeError(
            "falta MODEL_PATH en el entorno. Debe apuntar al artefacto .pt o al "
            "directorio que contiene active.json (lo escribe src.ai.train)."
        )

    destino = Path(bruto)
    if destino.is_file():
        return destino
    if destino.is_dir():
        puntero = destino / "active.json"
        if not puntero.is_file():
            raise RuntimeError(
                f"{destino} es un directorio pero no contiene active.json: todavía "
                "no se ha entrenado ningún modelo allí."
            )
        # La convención de las rutas escritas dentro de `active.json` vive en
        # `src.ai.model`: quien escribe (`src.ai.train`) y quien lee (este
        # servicio y `deploy/m1/check_artifact.py`) tienen que acordarla en un
        # solo sitio. Ahí está el detalle, incluido el bug que la obligó.
        try:
            return ruta_desde_puntero(puntero)
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
    raise RuntimeError(f"MODEL_PATH no es ni un archivo ni un directorio: {destino}")


# ---------------------------------------------------------------------------
# Aplicación
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _vida(app: FastAPI):
    """Carga el modelo **una vez** al arrancar y lo suelta al parar.

    Se carga en el arranque y no por petición: cargar un `state_dict` en cada
    `/predict` haría que el primer cliente pague el coste y que una
    corrupción del archivo se descubriera en horario de operación en lugar de
    al desplegar.
    """
    try:
        app.state.modelo = LocalPredictiveModel(str(ruta_del_artefacto()))
    except Exception as exc:  # noqa: BLE001 — se propaga al arrancar, a propósito
        raise RuntimeError(f"no se pudo cargar el modelo de inferencia: {exc}") from exc
    app.state.arrancado_en_ms = int(time.time() * 1000)
    yield
    app.state.modelo = None


app = FastAPI(
    title="RiTech — inferencia local",
    version="1.0.0",
    description="Servicio mínimo de predicción del NASDAQ-100 para la Máquina 1.",
    lifespan=_vida,
)


def _modelo(request: Request) -> LocalPredictiveModel:
    modelo: LocalPredictiveModel | None = getattr(request.app.state, "modelo", None)
    if modelo is None:
        raise HTTPException(status_code=503, detail="modelo no cargado")
    return modelo


@app.get("/health", response_model=EstadoSalida, tags=["salud"])
def salud(request: Request) -> EstadoSalida:
    """Liveness + qué modelo está sirviendo, incluida la versión de esquema.

    No solo devuelve "ok": declara **qué** modelo y con **qué** esquema de
    features, para que M2 pueda comprobar la compatibilidad sin tener que
    confiar en que alguien desplegó el artefacto correcto.
    """
    modelo = _modelo(request)
    return EstadoSalida(
        estado="ok",
        modelo_cargado=True,
        model_type=modelo.model_type,
        feature_schema_version=modelo.feature_schema_version,
        artifact_format_version=int(modelo.artifact_format),
        ventana=modelo.window,
        n_features=len(modelo.feature_names),
        dispositivo=str(next(modelo.model.parameters()).device),
        artefacto=str(modelo.model_path),
        subiendo_desde_ms=request.app.state.arrancado_en_ms,
    )


# ---------------------------------------------------------------------------
# Manejador de errores de validación
# ---------------------------------------------------------------------------

def _errores_de_validacion(exc: RequestValidationError) -> list[dict[str, str]]:
    """Reduce cada error de campo a su nombre, su mensaje y su tipo.

    Se descarta `input`, que es el valor crudo que envió el cliente. Puede ser
    enorme (una ventana entera) y puede no ser serializable, que es justo lo
    que provoca el fallo que este manejador viene a arreglar.
    """
    return [
        {
            "campo": ".".join(str(parte) for parte in error.get("loc", ())),
            "mensaje": str(error.get("msg", "")),
            "tipo": str(error.get("type", "")),
        }
        for error in exc.errors()
    ]


@app.exception_handler(RequestValidationError)
async def _manejar_validacion(request: Request,
                              exc: RequestValidationError) -> JSONResponse:
    """422 explicado, y no un 500 con el stack dentro.

    **Defecto encontrado al probar el servicio.** FastAPI, por defecto, incluye
    el cuerpo recibido dentro de la propia respuesta de error. Python sí parsea
    `Infinity` y `NaN` aunque no sean JSON válido, así que un cliente que
    mandara uno de esos valores producía una validación correcta (422) seguida
    de un fallo al serializar la respuesta de ese 422: el cliente terminaba
    recibiendo un **500 sin explicación** en lugar del error que sí correspondía.

    Aparte de eso, no conviene ecoar el cuerpo de la petición: aquí se manda
    una ventana de 120 floats y esa respuesta no tiene por qué repetirla.
    """
    del request  # el error ya lleva la ruta en `loc`
    return JSONResponse(
        status_code=422,
        content={"detail": _errores_de_validacion(exc)},
    )


@app.post("/predict", response_model=PrediccionSalida, tags=["inferencia"])
def predecir(ventana: VentanaFeatures, request: Request) -> PrediccionSalida:
    """Predice sobre una ventana sin normalizar. RF-304 / SDD §5.3.2.

    **No aplica ningún umbral**: devuelve el número y la razón. El corte al
    75 % y los perfiles de riesgo son del `RiskManager` (Sprint 4).
    """
    modelo = _modelo(request)
    matriz = np.asarray(ventana.features, dtype=np.float32)

    try:
        tendencia, confianza, rationale = modelo.run_inference(matriz)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    posterior = modelo.predict_proba(matriz)[0]
    return PrediccionSalida(
        trend_prediction=tendencia,
        confidence_score=confianza,
        technical_rationale=rationale,
        posterior_corregido={
            nombre: float(posterior[i]) for i, nombre in enumerate(LABEL_NAMES.values())
        },
        feature_schema_version=modelo.feature_schema_version,
        artifact_format_version=int(modelo.artifact_format),
        model_type=modelo.model_type,
        stable_band_pct=modelo.stable_band_pct,
        generado_en_ms=int(time.time() * 1000),
    )


def main() -> None:
    """Punto de entrada: `uvicorn src.ai.serve:app` con HOST/PORT del entorno."""
    import uvicorn

    host = os.environ.get("INFERENCE_HOST", "127.0.0.1")
    puerto = int(os.environ.get("INFERENCE_PORT", "8000"))
    # Por defecto solo loopback: el servicio habla con la Máquina 2 de la
    # intranet y no tiene por qué ser visible desde fuera de la Máquina 1.
    uvicorn.run(app, host=host, port=puerto, log_level="info")


if __name__ == "__main__":
    main()
