"""Pruebas del servicio de inferencia (`src/ai/serve.py`) — la frontera M2→M1.

Este servicio es lo único que corre en la Máquina 1. Lo que se protege aquí
son las tres cosas que pueden salir mal de formas caras:

1. **La normalización.** Si se aplicara fuera del modelo, M1 y M2 podrían
   normalizar distinto y nadie lo vería hasta que las predicciones cambiaran
   de signo.
2. **La confianza.** Debe ser el posterior corregido por prior, no la softmax
   cruda. Un `/predict` que devolviera la cruda entregaría al RiskManager un
   número que no dice lo que dice.
3. **El contrato de entrada.** Una ventana de 119 filas, con un `NaN`, o con
   un campo de más, debe morir en la puerta con un error claro.

Y una cuarta, que debe fallar ruidosamente: que no haya modelo cargado.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import torch

from src.ai.features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION, LABEL_NAMES, WINDOW
from src.ai.model import (
    DEFAULT_MODEL_CONFIG,
    LocalPredictiveModel,
    NasdaqMLP,
    build_artifact,
    save_artifact,
)

N_FEATURES = len(FEATURE_NAMES)
SEED = 20261017

#: Los pesos reales del entrenamiento del Sprint 3. Con ellos la confianza que
#: expone el servicio **no** puede coincidir con la softmax cruda.
PESOS_REALES = np.array([1.311, 0.397, 1.292], dtype=np.float64)

Ventana = list[list[float]]


def ventana() -> Ventana:
    return np.random.RandomState(SEED).randn(WINDOW, N_FEATURES).astype(np.float32).tolist()


@pytest.fixture()
def artefacto(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un modelo entrenado de mentira, con pesos de clase reales."""
    torch.manual_seed(SEED)
    artefacto = build_artifact(
        NasdaqMLP(), model_type="mlp", model_config=dict(DEFAULT_MODEL_CONFIG),
        class_weights=PESOS_REALES,
        stats_mean=np.zeros(N_FEATURES, dtype=np.float32),
        stats_std=np.ones(N_FEATURES, dtype=np.float32),
        stable_band_pct=0.10, trained_at_ms=0, training_device="cpu",
    )
    ruta = save_artifact(artefacto, tmp_path / "modelo.pt")
    monkeypatch.setenv("MODEL_PATH", str(ruta))
    return ruta


@pytest.fixture()
def cliente(artefacto: Path):
    from fastapi.testclient import TestClient
    from src.ai.serve import app

    with TestClient(app) as c:     # el context manager dispara el lifespan
        yield c


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

class TestSalud:
    def test_declara_que_esta_ok_y_que_modelo(self, cliente) -> None:
        cuerpo = cliente.get("/health").json()
        assert cliente.get("/health").status_code == 200
        assert cuerpo["estado"] == "ok"
        assert cuerpo["modelo_cargado"] is True
        assert cuerpo["model_type"] == "mlp"
        assert cuerpo["ventana"] == WINDOW
        assert cuerpo["n_features"] == N_FEATURES

    def test_declara_la_version_de_esquema_para_que_m2_lo_compruebe(self, cliente) -> None:
        """M2 debe poder verificar compatibilidad sin hacer la primera predicción."""
        assert cliente.get("/health").json()["feature_schema_version"] == FEATURE_SCHEMA_VERSION

    def test_declara_el_dispositivo_y_el_artefacto(self, cliente) -> None:
        cuerpo = cliente.get("/health").json()
        assert cuerpo["dispositivo"] in ("cpu", "cuda")
        assert Path(cuerpo["artefacto"]).is_file()

    def test_no_expone_variables_de_entorno(self, cliente) -> None:
        """El cuerpo de salud es un contrato, no un volcado de configuración."""
        cuerpo = cliente.get("/health").json()
        assert set(cuerpo) == {"estado", "modelo_cargado", "model_type",
                               "feature_schema_version", "artifact_format_version",
                               "ventana", "n_features", "dispositivo", "artefacto",
                               "subiendo_desde_ms"}


# ---------------------------------------------------------------------------
# /predict — contrato del SDD §5.3.2 / RF-304
# ---------------------------------------------------------------------------

class TestPredecir:
    def test_devuelve_la_tupla_literal_del_sdd(self, cliente) -> None:
        respuesta = cliente.post("/predict", json={"features": ventana()})
        assert respuesta.status_code == 200
        cuerpo = respuesta.json()
        assert cuerpo["trend_prediction"] in ("UP", "DOWN", "STABLE")
        assert isinstance(cuerpo["confidence_score"], float)
        assert isinstance(cuerpo["technical_rationale"], str)
        assert cuerpo["technical_rationale"].strip()

    def test_declara_su_esquema_en_cada_respuesta(self, cliente) -> None:
        """Además de en /health: cada predicción lleva su etiqueta de esquema."""
        cuerpo = cliente.post("/predict", json={"features": ventana()}).json()
        assert cuerpo["feature_schema_version"] == FEATURE_SCHEMA_VERSION
        assert cuerpo["artifact_format_version"] == 1
        assert cuerpo["model_type"] == "mlp"
        assert cuerpo["stable_band_pct"] == pytest.approx(0.10)

    def test_devuelve_el_posterior_corregido_de_las_tres_clases(self, cliente) -> None:
        cuerpo = cliente.post("/predict", json={"features": ventana()}).json()
        posterior = cuerpo["posterior_corregido"]
        assert set(posterior) == set(LABEL_NAMES.values())
        assert sum(posterior.values()) == pytest.approx(1.0, abs=1e-5)
        assert all(0.0 <= v <= 1.0 for v in posterior.values())

    def test_no_aplica_el_umbral_del_75_por_ciento(self, cliente) -> None:
        """RF-304 deja el umbral al RiskManager; el modelo solo informa."""
        cuerpo = cliente.post("/predict", json={"features": ventana()}).json()
        assert cuerpo["confidence_score"] >= 0.0
        assert "75" not in cuerpo["technical_rationale"]


# ---------------------------------------------------------------------------
# /predict — la confianza es el posterior corregido, no la softmax
# ---------------------------------------------------------------------------

class TestConfianzaCorregida:
    def test_la_confianza_es_el_posterior_corregido_de_la_clase_elegida(
            self, cliente, artefacto: Path) -> None:
        """Cruzado contra el modelo cargado fuera del servicio.

        Si el servicio devolviera la softmax cruda, este test fallaría: con
        estos pesos la corrección no es la identidad.
        """
        modelo = LocalPredictiveModel(str(artefacto))
        matriz = np.asarray(ventana(), dtype=np.float32)
        tendencia_directa, confianza_directa, _ = modelo.run_inference(matriz)

        cuerpo = cliente.post("/predict", json={"features": ventana()}).json()
        assert cuerpo["trend_prediction"] == tendencia_directa
        assert cuerpo["confidence_score"] == pytest.approx(confianza_directa, abs=1e-6)
        # Y es el valor de la clase elegida dentro del posterior, no el máximo
        # de un vector que puede tener de ganadora a otra.
        assert cuerpo["posterior_corregido"][tendencia_directa] == pytest.approx(
            confianza_directa, abs=1e-6
        )

    def test_el_posterior_que_devuelve_es_el_corregido(self, cliente, artefacto: Path) -> None:
        modelo = LocalPredictiveModel(str(artefacto))
        posterior_directo = modelo.predict_proba(np.asarray(ventana(), dtype=np.float32))[0]
        cuerpo = cliente.post("/predict", json={"features": ventana()}).json()
        for i, nombre in enumerate(LABEL_NAMES.values()):
            assert cuerpo["posterior_corregido"][nombre] == pytest.approx(
                float(posterior_directo[i]), abs=1e-6
            ), nombre


# ---------------------------------------------------------------------------
# /predict — validación estricta de la entrada
# ---------------------------------------------------------------------------

class TestValidacionDeEntrada:
    def test_rechaza_una_ventana_con_una_fila_de_menos(self, cliente) -> None:
        mala = ventana()[:-1]
        assert cliente.post("/predict", json={"features": mala}).status_code == 422

    def test_rechaza_una_ventana_con_una_columna_de_mas(self, cliente) -> None:
        mala = [fila + [0.0] for fila in ventana()]
        assert cliente.post("/predict", json={"features": mala}).status_code == 422

    def test_rechaza_una_ventana_plana(self, cliente) -> None:
        """La forma total es 120 en los dos casos; sin comprobación pasaría."""
        plana = [0.0] * (WINDOW * N_FEATURES)
        assert cliente.post("/predict", json={"features": plana}).status_code == 422

    def test_rechaza_una_ventana_vacia(self, cliente) -> None:
        assert cliente.post("/predict", json={"features": []}).status_code == 422

    def test_rechaza_una_ventana_con_nan(self, cliente) -> None:
        """Se envía el cuerpo crudo a propósito.

        `json=` de httpx se niega a serializar `NaN`, pero el `json.loads` de
        Python **sí lo acepta** al parsear: un cliente escrito en Python puede
        mandarlo y un servidor escrito en Python lo recibe como `float('nan')`
        sin protestar. Ese es exactamente el camino por el que un `NaN` llega
        a una predicción, y por eso la comprobación de finititud es del
        servidor y no una cosa del cliente.
        """
        mala = ventana()
        mala[0][0] = float("nan")
        respuesta = cliente.post(
            "/predict", content=json.dumps({"features": mala}, allow_nan=True),
            headers={"content-type": "application/json"},
        )
        assert respuesta.status_code == 422
        # No basta con que falle: tiene que fallar POR EL NaN y no por otra
        # cosa, si no el test no prueba nada.
        assert "NaN o infinito" in str(respuesta.json()["detail"])

    def test_un_infinito_no_produce_un_500(self, cliente) -> None:
        """Regresión: el manejador de errores de FastAPI se cayó con el cuerpo.

        FastAPI incluye el cuerpo recibido dentro de la respuesta de error del
        `RequestValidationError`. Como Python sí parsea `Infinity` aunque no sea
        JSON válido, la validación correcta (422) iba seguida de un fallo al
        serializar esa propia respuesta de error, y el cliente recibía un
        **500 sin explicación** en lugar del 422 que correspondía. Este test
        es la regresión.
        """
        mala = ventana()
        mala[5][3] = float("inf")
        respuesta = cliente.post(
            "/predict", content=json.dumps({"features": mala}, allow_nan=True),
            headers={"content-type": "application/json"},
        )
        assert respuesta.status_code == 422
        assert "NaN o infinito" in str(respuesta.json()["detail"])

    def test_el_error_de_validacion_no_ecoa_el_cuerpo_recibido(self, cliente) -> None:
        """El cuerpo es una ventana de 120 floats; repetirla no le sirve a nadie."""
        mala = ventana()
        mala[0][0] = float("nan")
        cuerpo = cliente.post(
            "/predict", content=json.dumps({"features": mala}, allow_nan=True),
            headers={"content-type": "application/json"},
        ).json()
        assert cuerpo["detail"][0]["campo"].endswith("features")
        assert cuerpo["detail"][0]["tipo"] == "value_error"
        assert "input" not in cuerpo["detail"][0]

    def test_rechaza_una_ventana_con_texto(self, cliente) -> None:
        mala = ventana()
        mala[2][2] = "no soy un número"
        assert cliente.post("/predict", json={"features": mala}).status_code == 422

    def test_rechaza_campos_desconocidos(self, cliente) -> None:
        """`extra="forbid"`: un campo que nadie esperaba no debe pasarse por alto."""
        assert cliente.post(
            "/predict", json={"features": ventana(), "temperatura": 0.9}
        ).status_code == 422

    def test_el_mensaje_explica_lo_que_falta(self, cliente) -> None:
        cuerpo = cliente.post("/predict", json={"features": ventana()[:-1]}).json()
        detalle = str(cuerpo["detail"])
        assert f"{WINDOW}×{N_FEATURES}" in detalle


# ---------------------------------------------------------------------------
# Resolución del artefacto — configuración por entorno, sin rutas en el código
# ---------------------------------------------------------------------------

class TestRutaDelArtefacto:
    def test_un_archivo_directo_se_usa_tal_cual(self, artefacto: Path) -> None:
        from src.ai.serve import ruta_del_artefacto
        assert ruta_del_artefacto() == artefacto

    def test_un_directorio_se_resuelve_por_active_json(
            self, tmp_path: Path, artefacto: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Así el reentrenamiento diario puede dejar un modelo nuevo y reiniciar.

        Es lo que hace `src.ai.train`: escribe el `.pt` y apunta `active.json`
        a él. El servicio solo tiene que apuntar al directorio.
        """
        from src.ai.serve import ruta_del_artefacto
        (tmp_path / "activos").mkdir()
        destinos = tmp_path / "activos"
        import shutil
        shutil.copy(artefacto, destinos / "modelo.pt")
        (destinos / "active.json").write_text(
            json.dumps({"path": "modelo.pt", "arquitectura": "mlp"}), encoding="utf-8"
        )
        monkeypatch.setenv("MODEL_PATH", str(destinos))
        assert ruta_del_artefacto() == (destinos / "modelo.pt").resolve()

    def test_un_directorio_sin_active_json_no_servira(
            self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.ai.serve import ruta_del_artefacto
        vacio = tmp_path / "vacio"
        vacio.mkdir()
        monkeypatch.setenv("MODEL_PATH", str(vacio))
        with pytest.raises(RuntimeError, match="active.json"):
            ruta_del_artefacto()

    def test_sin_model_path_no_se_arranca(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Fallar ruidosamente al arrancar, no en la primera petición."""
        from src.ai.serve import ruta_del_artefacto
        monkeypatch.delenv("MODEL_PATH", raising=False)
        with pytest.raises(RuntimeError, match="MODEL_PATH"):
            ruta_del_artefacto()

    def test_una_ruta_inexistente_no_se_arranca(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from src.ai.serve import ruta_del_artefacto
        monkeypatch.setenv("MODEL_PATH", "/no/existe/nada.pt")
        with pytest.raises(RuntimeError, match="ni un archivo ni un directorio"):
            ruta_del_artefacto()

    def test_el_servicio_no_arranca_si_el_artefacto_es_malo(
            self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Un artefacto corrupto debe descubrirse al desplegar, no en operación."""
        from fastapi.testclient import TestClient
        from src.ai.serve import app
        roto = tmp_path / "roto.pt"
        roto.write_bytes(b"esto no es un state_dict")
        monkeypatch.setenv("MODEL_PATH", str(roto))
        with pytest.raises(RuntimeError, match="no se pudo cargar"):
            with TestClient(app):
                pass
