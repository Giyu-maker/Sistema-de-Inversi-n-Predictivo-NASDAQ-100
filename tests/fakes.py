"""Dobles de prueba compartidos por los tests de la Fase B.

Todos implementan los `Protocol` de producción (`LectorVelas`,
`RepositorioPredicciones`, `RepositorioConfiguraciones`, `ClienteInferencia`),
así que si un test deja de pasar es porque cambió el contrato, no porque el
doble siga comportamiento viejo.

Ninguno toca red, PostgreSQL ni InfluxDB: los tests de esta fase son
herméticos, igual que los del Sprint 2.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import numpy as np
from fastapi.testclient import TestClient

from src.ai.client import Prediccion
from src.ai.features import FEATURE_SCHEMA_VERSION
from src.database.configuraciones import (
    ConfiguracionSistema,
    configuracion_por_defecto,
)
from src.database.predictions import PrediccionGuardada, PrediccionNueva
from src.endpoints.app import crear_app
from src.endpoints.dependencias import EstadoAplicacion
from src.ingestion.engine import NasdaqCandle
from src.utils.fechas import ZONA_COLOMBIA

#: Vela de 5 minutos (RF-101) en epoch ms.
INTERVALO_MS = 5 * 60 * 1000

#: Viernes 16/10/2026, 10:00 en Colombia = 11:00 en Nueva York: en sesión.
AHORA_EN_SESION = datetime(2026, 10, 16, 10, 0, tzinfo=ZONA_COLOMBIA)
#: Sábado: fuera de sesión por fin de semana.
AHORA_FIN_DE_SEMANA = datetime(2026, 10, 17, 10, 0, tzinfo=ZONA_COLOMBIA)

#: epoch ms de una apertura de viernes (16/10/2026, 09:30 ET).
APERTURA_MS = int(datetime(2026, 10, 16, 9, 30, tzinfo=ZONA_COLOMBIA).timestamp() * 1000)


def vela(ts_ms: int, precio: float = 500.0) -> NasdaqCandle:
    """Vela sintética coherente (high ≥ close ≥ low)."""
    return NasdaqCandle(
        timestamp=ts_ms,
        price_open=precio,
        price_high=precio + 1.0,
        price_low=precio - 1.0,
        price_close=precio,
        volume=1_000.0,
    )


def serie_sesion(inicio_ms: int, n: int, precio_inicial: float = 500.0) -> list[NasdaqCandle]:
    """`n` velas consecutivas de 5 min desde `inicio_ms` (una sesión sin huecos)."""
    return [vela(inicio_ms + i * INTERVALO_MS, precio_inicial + i) for i in range(n)]


def prediccion_falsa(
    trend: str = "STABLE", confianza: float = 0.83
) -> Prediccion:
    """`Prediccion` de M1 conforme al contrato, lista para el ciclo."""
    return Prediccion(
        trend_prediction=trend,
        confidence_score=confianza,
        technical_rationale="Cruce de medias con volumen creciente.",
        posterior_corregido={"DOWN": 0.08, "STABLE": 0.09, "UP": 0.83},
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        model_type="mlp",
        stable_band_pct=0.1,
        generado_en_ms=1,
        latencia_ms=1,
    )


class LectorFalso:
    """Serie en memoria; `error` simula InfluxDB caído."""

    def __init__(
        self, velas: list[NasdaqCandle] | None = None, error: Exception | None = None
    ) -> None:
        self.velas = list(velas or [])
        self.error = error
        self.consultas_ultimas: list[int] = []
        self.consultas_rango: list[tuple[int, int, str, int | None]] = []

    def ultimas_velas(self, n: int) -> list[NasdaqCandle]:
        self.consultas_ultimas.append(n)
        if self.error:
            raise self.error
        return self.velas[-n:]

    def velas_rango(
        self, start_ms: int, stop_ms: int, *, agregacion: str = "5m", limit: int | None = None
    ) -> list[NasdaqCandle]:
        self.consultas_rango.append((start_ms, stop_ms, agregacion, limit))
        if self.error:
            raise self.error
        rango = [v for v in self.velas if start_ms <= v.timestamp < stop_ms]
        return rango[:limit] if limit else rango

    def ultima_vela(self) -> NasdaqCandle | None:
        if self.error:
            raise self.error
        return self.velas[-1] if self.velas else None


class PrediccionesFalso:
    """Filas en memoria con el mismo contrato que `PrediccionesPostgres`."""

    def __init__(self, error: Exception | None = None) -> None:
        self.filas: list[PrediccionGuardada] = []
        self.error = error
        self.intentos: list[PrediccionNueva] = []

    def guardar(self, pred: PrediccionNueva) -> PrediccionGuardada | None:
        self.intentos.append(pred)
        if self.error:
            raise self.error
        if any(f.prediction_time == pred.prediction_time for f in self.filas):
            return None
        fila = PrediccionGuardada(
            id=f"fake-{len(self.filas) + 1}",
            prediction_time=pred.prediction_time,
            trend_prediction=pred.trend_prediction,
            confidence_score=pred.confidence_score,
            technical_rationale=pred.technical_rationale,
        )
        self.filas.append(fila)
        return fila

    def ultima(self) -> PrediccionGuardada | None:
        if self.error:
            raise self.error
        if not self.filas:
            return None
        return max(self.filas, key=lambda f: f.prediction_time)

    def listar(self, limit: int, offset: int) -> tuple[list[PrediccionGuardada], int]:
        if self.error:
            raise self.error
        ordenadas = sorted(self.filas, key=lambda f: f.prediction_time, reverse=True)
        return ordenadas[offset:offset + limit], len(ordenadas)


class ConfiguracionesFalso:
    def __init__(
        self,
        configuracion: ConfiguracionSistema | None = None,
        error: Exception | None = None,
    ) -> None:
        self.configuracion = configuracion
        self.error = error
        self.seembradas = 0

    def leer(self) -> ConfiguracionSistema | None:
        if self.error:
            raise self.error
        return self.configuracion

    def sembrar_inicial(self) -> bool:
        if self.error:
            raise self.error
        if self.configuracion is not None:
            return False
        self.configuracion = configuracion_por_defecto()
        self.seembradas += 1
        return True


class ClienteIaFalso:
    """Respuestas predeterminadas de `/health` y `/modelo` de M1 (para el API)."""

    def __init__(
        self,
        modelo_cargado: bool = True,
        cuerpo_modelo: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.modelo_cargado = modelo_cargado
        self.cuerpo_modelo = cuerpo_modelo or CUERPO_MODELO_EJEMPLO
        self.error = error
        self.consultas_salud = 0
        self.consultas_modelo = 0

    def salud(self) -> dict[str, Any]:
        self.consultas_salud += 1
        if self.error:
            raise self.error
        return {
            "estado": "ok",
            "servicio": "inference",
            "modelo_cargado": self.modelo_cargado,
        }

    def modelo(self) -> dict[str, Any]:
        self.consultas_modelo += 1
        if self.error:
            raise self.error
        return self.cuerpo_modelo


class ClienteInferenciaFalso:
    """`predecir()` para el ciclo de M2: devuelve `Prediccion` o lanza."""

    def __init__(
        self, prediccion: Prediccion | None = None, error: Exception | None = None
    ) -> None:
        self.prediccion = prediccion or prediccion_falsa()
        self.error = error
        self.ventanas_recibidas: list[Any] = []

    def predecir(self, ventana: Any) -> Prediccion:
        self.ventanas_recibidas.append(ventana)
        if self.error:
            raise self.error
        return self.prediccion


#: Contrato de `GET /modelo` (M1) con los números del informe Sprint 3.
CUERPO_MODELO_EJEMPLO: dict[str, Any] = {
    "model_type": "mlp",
    "feature_schema_version": FEATURE_SCHEMA_VERSION,
    "ventana": 12,
    "horizonte": 3,
    "banda_stable_pct": 0.1,
    "entrenado_en_ms": 1792289791093,
    "dispositivo_entrenamiento": "cpu",
    "macro_f1_test": 0.4312686359675911,
    "acierto_direccional": {
        "ratio": 0.4728888888888889,
        "n": 1125,
        "aciertos": 532,
        "ic_95": [0.4387, 0.5078],
        "fuente": "informe_entrenamiento",
        "ventaja_demostrada": False,
    },
}


def estado_de_prueba(
    *,
    velas: list[NasdaqCandle] | None = None,
    lector: LectorFalso | None = None,
    predicciones: PrediccionesFalso | None = None,
    configuraciones: ConfiguracionesFalso | None = None,
    cliente_ia: ClienteIaFalso | None = None,
    ahora: datetime = AHORA_EN_SESION,
    feriados: frozenset[date] = frozenset(),
    banda_stable_pct: float = 0.1,
) -> EstadoAplicacion:
    """Estado completo con fakes y sin caché, listo para `crear_app`."""
    return EstadoAplicacion(
        lector=lector if lector is not None else LectorFalso(velas),
        predicciones=predicciones if predicciones is not None else PrediccionesFalso(),
        configuraciones=(
            configuraciones if configuraciones is not None else ConfiguracionesFalso()
        ),
        cliente_ia=cliente_ia if cliente_ia is not None else ClienteIaFalso(),
        ahora=lambda: ahora,
        feriados=feriados,
        banda_stable_pct=banda_stable_pct,
        # Sin caché: cada petición ve el estado real de los dobles.
        cache_ia_s=0.0,
        cache_calidad_s=0.0,
    )


def cliente_de_prueba(estado: EstadoAplicacion) -> TestClient:
    """TestClient sobre la app real, con el estado falsificado."""
    return TestClient(crear_app(estado))


def ventana_np(n: int = 12) -> np.ndarray:
    """Ventana `(n, N_FEATURES)` para quien necesite validar el transporte."""
    from src.ai.features import N_FEATURES

    return np.zeros((n, N_FEATURES), dtype=np.float64)
