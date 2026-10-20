"""Contrato de salida de la API de M2 — Pydantic estricto (RNF-011).

Dos reglas de nomenclatura conviven aquí, a propósito:

* **Los nombres del SRS/SDD se mantienen literales** (`trend_prediction`,
  `confidence_score`, `technical_rationale`): son la frontera del contrato de
  RF-304 y ya los usa `src/ai/serve.py`. Cambiarlos aquí sería romper el
  contrato con quien ya lo consume.
* **Lo añadido en esta fase va en español** (`motivo`, `senal`, `fecha`,
  `resultado_real`): son mensajes que lee el operador y la interfaz.

Todo es `extra="forbid"`: una respuesta con un campo que nadie esperaba es un
cambio de contrato, y un cambio de contrato debe romper el test, no pasar
desapercibido.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

ESTADOS_SISTEMA = ("VERDE", "AMARILLO", "CERRADO")
ESTADOS_RESULTADO = ("DISPONIBLE", "PENDIENTE", "NO_APLICA")


class _Salida(BaseModel):
    model_config = ConfigDict(extra="forbid")


# -- salud ------------------------------------------------------------------

class HealthSalida(_Salida):
    estado: str = "ok"
    servicio: str = "api"
    version: str = "0.1.0"


# -- piezas comunes ---------------------------------------------------------

class VelaSalida(_Salida):
    timestamp: int  #: epoch ms
    fecha: str  #: DD/MM/YYYY HH:mm:ss (hora de Colombia)
    open: float = Field(ge=0)
    high: float = Field(ge=0)
    low: float = Field(ge=0)
    close: float = Field(ge=0)
    volume: float = Field(ge=0)


class VelaResumenSalida(_Salida):
    """Vela sin OHLC completo: para el estado y el rendimiento basta cierre y hora."""

    timestamp: int
    fecha: str
    precio: float
    antiguedad_s: int | None = None


class SenalSalida(_Salida):
    """Veredicto calculado en el backend (`src/security/perfiles.py`, D7)."""

    operable: bool
    motivo: str
    umbral: float
    perfil: str
    tendencia: str | None
    confianza: float | None


class ResultadoRealSalida(_Salida):
    estado: str
    motivo: str
    retorno_pct: float | None
    etiqueta: str | None
    banda_pct: float | None


class PrediccionSalida(_Salida):
    id: str
    prediction_time: int  #: epoch ms del cierre de la última vela de la ventana (D2)
    fecha: str
    trend_prediction: str
    confidence_score: float
    technical_rationale: str
    resultado_real: ResultadoRealSalida | None = None


# -- /api/status ------------------------------------------------------------

class MercadoSalida(_Salida):
    abierto: bool
    motivo: str
    feriado: bool
    zona: str
    horario: str


class IaSalida(_Salida):
    disponible: bool
    detalle: str


class ModoSalida(_Salida):
    nombre: str
    descripcion: str
    autonomo_disponible: bool
    motivo_bloqueo: str


class PerfilSalida(_Salida):
    nombre: str
    umbral_confianza: float
    max_loss_day: float
    #: True cuando el valor viene de los por defecto y no de la tabla.
    por_defecto: bool


class EstadoSistemaSalida(_Salida):
    estado: str
    motivo: str
    mercado: MercadoSalida
    ultima_vela: VelaResumenSalida | None
    retraso_max_s: int
    ia: IaSalida
    modo: ModoSalida
    perfil: PerfilSalida
    ultima_prediccion: PrediccionSalida | None
    senal: SenalSalida | None
    avisos: list[str]


# -- /api/candles -----------------------------------------------------------

class VelasSalida(_Salida):
    velas: list[VelaSalida]
    total: int
    aggregate: str
    nota: str


# -- /api/predictions -------------------------------------------------------

class PrediccionUltimaSalida(_Salida):
    encontrada: bool
    prediccion: PrediccionSalida | None
    senal: SenalSalida
    #: Avisos de configuración/infraestructura (PG caído, perfil por defecto…).
    #: Difíciles de completar en `/api/status` (10 s de sondeo): para eso existe
    #: este endpoint, que es el que la interfaz consulta al abrir el detalle.
    avisos: list[str] = Field(default_factory=list)


class PrediccionPaginaSalida(_Salida):
    predicciones: list[PrediccionSalida]
    total: int
    limit: int
    offset: int
    avisos: list[str] = Field(default_factory=list)


# -- /api/yield -------------------------------------------------------------

class RendimientoSalida(_Salida):
    virtual: bool
    nota: str
    disponible: bool
    motivo: str | None = None
    apertura_sesion: VelaResumenSalida | None = None
    ultima_vela: VelaResumenSalida | None = None
    variacion_nasdaq_pct: float | None = None
    rendimiento_ritech_pct: float | None = None
    limite_perdida_pct: float
    umbral_perdida_alcanzado: bool


# -- /api/model-quality -----------------------------------------------------

class AciertoDireccionalSalida(_Salida):
    ratio: float | None
    n: int | None
    aciertos: int | None
    ic_95: list[float] | None
    fuente: str
    #: False si el intervalo contiene el 50 % (no hay ventaja demostrada).
    ventaja_demostrada: bool | None


class CalidadModeloSalida(_Salida):
    disponible: bool
    motivo: str | None = None
    model_type: str | None = None
    feature_schema_version: str | None = None
    entrenado_en_ms: int | None = None
    entrenado_en: str | None = None
    dispositivo_entrenamiento: str | None = None
    macro_f1_test: float | None = None
    banda_stable_pct: float | None = None
    acierto_direccional: AciertoDireccionalSalida | None = None
    nota: str
