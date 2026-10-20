"""Estado de la aplicación: con quién habla cada petición y con qué caché.

El API no construye clientes dentro de los endpoints: todo lo que necesita lo
recibe en `EstadoAplicacion`, que se monta **una vez** al arrancar (o que los
tests inyectan con fakes). Así un endpoint se prueba montando una app con
objetos falsos, sin red, sin InfluxDB y sin PostgreSQL.

`ClienteInferencia` es un `Protocol` en lugar de `InferenceClient` concreto: al
API solo le importan `salud()` y `modelo()`, y esa es exactamente la superficie
que un fake tiene que reimplementar.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Protocol

from fastapi import Request
from influxdb_client import InfluxDBClient

from src.ai.client import ErrorDeInferencia, InferenceClient
from src.database.configuraciones import (
    ConfiguracionSistema,
    ConfiguracionesPostgres,
    RepositorioConfiguraciones,
    configuracion_por_defecto,
)
from src.database.influx_lectura import LectorInflux, LectorVelas
from src.database.predictions import (
    PrediccionGuardada,
    PrediccionesPostgres,
    RepositorioPredicciones,
)
from src.endpoints.esquemas import (
    AciertoDireccionalSalida,
    CalidadModeloSalida,
    IaSalida,
)
from src.security.perfiles import (
    Senal,
    evaluar_senal,
    perfil_valido,
    senal_sin_prediccion,
)
from src.utils.fechas import ZONA_COLOMBIA, fecha_hora
from src.utils.mercado import feriados_desde_entorno

logger = logging.getLogger(__name__)

#: Nota que acompaña siempre la calidad del modelo (honestidad de la UI).
NOTA_CALIDAD = (
    "Modelo local sin ventaja direccional demostrada: la interfaz no emite "
    "órdenes de compra ni de venta, solo tendencia con su nivel de confianza."
)


class ClienteInferencia(Protocol):
    """Superficie del cliente de M1 que consume el API."""

    def salud(self) -> dict[str, Any]: ...

    def modelo(self) -> dict[str, Any]: ...


@dataclass
class Cache:
    """Caché de un solo valor con TTL. `ttl_s=0` la desactiva (tests)."""

    ttl_s: float
    valor: Any | None = None
    guardado_en_s: float = 0.0

    def obtener(self) -> Any | None:
        if self.ttl_s <= 0:
            return None
        if self.valor is None:
            return None
        return self.valor if (time.monotonic() - self.guardado_en_s) <= self.ttl_s else None

    def guardar(self, valor: Any) -> Any | None:
        if self.ttl_s <= 0:
            return valor
        self.valor = valor
        self.guardado_en_s = time.monotonic()
        return valor


@dataclass
class EstadoAplicacion:
    """Todo lo que los endpoints necesitan, inyectado desde `crear_app`."""

    lector: LectorVelas
    predicciones: RepositorioPredicciones
    configuraciones: RepositorioConfiguraciones
    cliente_ia: ClienteInferencia | None
    #: Reloj inyectable: los tests fijan la hora para probar antigüedad y sesión.
    ahora: Callable[[], datetime]
    feriados: frozenset[date] = frozenset()
    #: RF-101: 300 s. El estado marca desactualizado a los 2 intervalos.
    intervalo_vela_s: int = 300
    #: Banda STABLE con la que entrenó el modelo (`stable_band_pct` del artefacto).
    banda_stable_pct: float = 0.1
    cache_ia_s: float = 5.0
    cache_calidad_s: float = 60.0
    cache_ia: Cache = field(init=False)
    cache_calidad: Cache = field(init=False)

    def __post_init__(self) -> None:
        self.cache_ia = Cache(self.cache_ia_s)
        self.cache_calidad = Cache(self.cache_calidad_s)

    @property
    def retraso_max_s(self) -> int:
        """2 intervalos de vela: la regla provisional documentada (B1)."""
        return 2 * self.intervalo_vela_s


def estado_app(request: Request) -> EstadoAplicacion:
    """Dependencia FastAPI del estado.

    Si la app no recibió estado explícito (arranque normal con
    `src.endpoints.app:app`), se construye **una vez**, en la primera petición:
    así leer el entorno y crear los clientes no ocurre al importar el módulo, y
    un test que importe `app` no se lleva por delante el entorno real.
    """
    estado: EstadoAplicacion | None = getattr(request.app.state, "riTech", None)
    if estado is None:
        estado = estado_desde_entorno()
        request.app.state.riTech = estado
    return estado


def estado_desde_entorno() -> EstadoAplicacion:
    """Clientes reales del entorno (§15.1): nada escrito en el código.

    La inferencia es **opcional** aquí: si falta `INFERENCE_URL` el API sigue
    siendo útil (velas, rendimiento e historial) y el estado informará de «IA no
    disponible» en lugar de no arrancar.
    """
    influx = InfluxDBClient(
        url=os.environ.get("INFLUXDB_URL", "http://127.0.0.1:8086"),
        token=os.environ.get("INFLUXDB_ADMIN_TOKEN") or os.environ.get("INFLUXDB_TOKEN") or "",
        org=os.environ.get("INFLUXDB_ORG", "ritech"),
    )
    try:
        cliente_ia: InferenceClient | None = InferenceClient()
    except ValueError as exc:
        logger.warning("API sin servicio de inferencia: %s", exc)
        cliente_ia = None

    return EstadoAplicacion(
        lector=LectorInflux(influx),
        predicciones=PrediccionesPostgres(),
        configuraciones=ConfiguracionesPostgres(),
        cliente_ia=cliente_ia,
        ahora=lambda: datetime.now(tz=ZONA_COLOMBIA),
        feriados=feriados_desde_entorno(),
        intervalo_vela_s=int(os.environ.get("INTERVALO_VELA_S", "300")),
        banda_stable_pct=float(os.environ.get("BANDA_STABLE_PCT", "0.1")),
        cache_ia_s=float(os.environ.get("CACHE_IA_S", "5")),
        cache_calidad_s=float(os.environ.get("CACHE_CALIDAD_S", "60")),
    )


def consultar_ia(estado: EstadoAplicacion) -> IaSalida:
    """¿Responde el servicio de inferencia de M1? Con caché corta.

    La respuesta de `/health` se comprueba aquí y no se da por buena: un
    servicio vivo con el modelo sin cargar **no** está disponible para predecir.
    """
    cacheada = estado.cache_ia.obtener()
    if cacheada is not None:
        return cacheada

    if estado.cliente_ia is None:
        salida = IaSalida(disponible=False, detalle="Sin servicio de inferencia configurado.")
    else:
        try:
            cuerpo = estado.cliente_ia.salud()
        except ErrorDeInferencia as exc:
            salida = IaSalida(disponible=False, detalle=f"Inferencia no disponible: {exc}")
        except Exception as exc:  # noqa: BLE001 — cualquier fallo de red es "no disponible"
            salida = IaSalida(disponible=False, detalle=f"Inferencia no disponible: {exc}")
        else:
            cargado = bool(cuerpo.get("modelo_cargado"))
            salida = IaSalida(
                disponible=cargado,
                detalle=(
                    "Servicio de inferencia activo y modelo cargado."
                    if cargado
                    else "El servicio responde pero el modelo no está cargado."
                ),
            )
    return estado.cache_ia.guardar(salida)


def consultar_calidad(estado: EstadoAplicacion) -> CalidadModeloSalida:
    """`GET /modelo` de M1 → nota de calidad del modelo, con caché corta.

    Si M1 no responde el resultado no es un error HTTP: es un **estado
    resuelto** (`disponible=false`) que la UI puede pintar como «calidad no
    disponible». Lo que nunca hace es inventar métricas.
    """
    cacheada = estado.cache_calidad.obtener()
    if cacheada is not None:
        return cacheada

    if estado.cliente_ia is None:
        salida = CalidadModeloSalida(
            disponible=False,
            motivo="Sin servicio de inferencia configurado.",
            nota=NOTA_CALIDAD,
        )
        return estado.cache_calidad.guardar(salida)

    try:
        cuerpo = estado.cliente_ia.modelo()
    except ErrorDeInferencia as exc:
        salida = CalidadModeloSalida(
            disponible=False, motivo=f"Inferencia no disponible: {exc}", nota=NOTA_CALIDAD
        )
        return estado.cache_calidad.guardar(salida)
    except Exception as exc:  # noqa: BLE001 — idem
        salida = CalidadModeloSalida(
            disponible=False, motivo=f"Inferencia no disponible: {exc}", nota=NOTA_CALIDAD
        )
        return estado.cache_calidad.guardar(salida)

    try:
        salida = _a_calidad(cuerpo)
    except (TypeError, ValueError, KeyError) as exc:
        # Respuesta que no cumple el contrato de `/modelo`: versión desalineada
        # o un servicio que no es el nuestro. Se informa, no se interpreta.
        salida = CalidadModeloSalida(
            disponible=False,
            motivo=f"Respuesta inesperada del servicio de inferencia: {exc}",
            nota=NOTA_CALIDAD,
        )
    return estado.cache_calidad.guardar(salida)


def _a_calidad(cuerpo: dict[str, Any]) -> CalidadModeloSalida:
    """Contrato de `GET /modelo` (M1) → modelo de salida estricto."""
    macro = cuerpo.get("macro_f1_test")
    return CalidadModeloSalida(
        disponible=True,
        motivo=None,
        model_type=str(cuerpo["model_type"]),
        feature_schema_version=str(cuerpo["feature_schema_version"]),
        entrenado_en_ms=int(cuerpo["entrenado_en_ms"]),
        entrenado_en=fecha_hora(int(cuerpo["entrenado_en_ms"])),
        dispositivo_entrenamiento=str(cuerpo.get("dispositivo_entrenamiento", "")),
        macro_f1_test=float(macro) if macro is not None else None,
        banda_stable_pct=float(cuerpo["banda_stable_pct"]),
        acierto_direccional=_acierto(cuerpo.get("acierto_direccional") or {}),
        nota=NOTA_CALIDAD,
    )


def _acierto(datos: dict[str, Any]) -> AciertoDireccionalSalida | None:
    if not datos:
        return None
    ratio, n, aciertos = datos.get("ratio"), datos.get("n"), datos.get("aciertos")
    return AciertoDireccionalSalida(
        ratio=float(ratio) if ratio is not None else None,
        n=int(n) if n is not None else None,
        aciertos=int(aciertos) if aciertos is not None else None,
        ic_95=[float(v) for v in datos["ic_95"]] if datos.get("ic_95") is not None else None,
        fuente=str(datos.get("fuente", "desconocida")),
        ventaja_demostrada=(
            bool(datos["ventaja_demostrada"])
            if datos.get("ventaja_demostrada") is not None
            else None
        ),
    )


@dataclass(frozen=True)
class ConfiguracionVigente:
    """Configuración del sistema + lo que haya que avisar sobre ella.

    El API no puede dejar de responder porque PostgreSQL esté caído: en ese
    caso contesta con los valores por defecto y **lo dice** (`avisos`,
    `postgres_ok` y `por_defecto`). Lo que no hace es presentar un valor por
    defecto como si hubiera salido de la tabla.
    """

    configuracion: ConfiguracionSistema
    avisos: list[str]
    postgres_ok: bool
    por_defecto: bool


def configuracion_activa(estado: EstadoAplicacion) -> ConfiguracionVigente:
    """Configuración vigente con su umbral derivado del perfil activo (RF-403)."""
    try:
        configuracion = estado.configuraciones.leer()
    except Exception as exc:  # noqa: BLE001 — PostgreSQL caído no puede tumbar la API
        return ConfiguracionVigente(
            configuracion=configuracion_por_defecto(),
            avisos=[f"PostgreSQL no responde: se usan los valores por defecto ({exc})."],
            postgres_ok=False,
            por_defecto=True,
        )
    if configuracion is None:
        return ConfiguracionVigente(
            configuracion=configuracion_por_defecto(),
            avisos=[
                "Sin fila de configuración en la base de datos: valores por "
                "defecto (Modo Asesor, perfil MODERATE)."
            ],
            postgres_ok=True,
            por_defecto=True,
        )
    if not perfil_valido(configuracion.risk_profile):
        return ConfiguracionVigente(
            configuracion=configuracion_por_defecto(),
            avisos=[
                f"Perfil de riesgo desconocido en la configuración "
                f"({configuracion.risk_profile!r}): se usa MODERATE."
            ],
            postgres_ok=True,
            por_defecto=True,
        )
    avisos: list[str] = []
    if configuracion.is_autonomous:
        avisos.append(
            "La configuración marca Modo Autónomo: esta fase no implementa su "
            "ejecución (Sprint 4) y no se envían órdenes."
        )
    return ConfiguracionVigente(
        configuracion=configuracion, avisos=avisos, postgres_ok=True, por_defecto=False
    )


def senal_de(prediccion: PrediccionGuardada, perfil: str, avisos: list[str]) -> Senal:
    """Veredicto de señal para una predicción ya persistida (RF-403, D7).

    Una fila con tendencia o confianza corrupta no puede tumbar la petición
    entera: se avisa y se responde «sin señal», que es literalmente lo que hay.
    """
    try:
        return evaluar_senal(
            prediccion.trend_prediction, prediccion.confidence_score, perfil
        )
    except ValueError as exc:
        avisos.append(f"No se pudo evaluar la señal de la predicción: {exc}")
        return senal_sin_prediccion(perfil)
