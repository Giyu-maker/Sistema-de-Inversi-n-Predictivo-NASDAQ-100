"""Pruebas del modelo de inferencia (`src/ai/model.py`) y su XAI (`src/ai/xai.py`).

Lo que se protege aquí no es que "la red devuelve números", sino tres cosas que
rompen el sistema de formas silenciosa:

1. Que la normalización que se aplica sea **la del artefacto** y no otra.
2. Que un artefacto entrenado con otro esquema de features se **rechace** en
   lugar de servirse (contrato M2 → M1).
3. Que `run_inference` cumpla la tupla literal del SDD §5.3.2.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import torch

from src.ai.features import (
    FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION,
    HORIZON,
    LABEL_NAMES,
    WINDOW,
)
from src.ai.model import (
    ARTIFACT_FORMAT_VERSION,
    DEFAULT_LINEAR_CONFIG,
    DEFAULT_MODEL_CONFIG,
    LocalPredictiveModel,
    NasdaqLinear,
    NasdaqMLP,
    SchemaVersionError,
    build_artifact,
    check_schema_version,
    load_artifact,
    make_model,
    save_artifact,
)
from src.ai.xai import (
    FEATURE_GROUPS,
    attribution_by_group,
    attribution_by_position,
    build_rationale,
    gradient_x_input,
    group_of,
)

N_FEATURES = len(FEATURE_NAMES)
SEED = 20261017

#: Pesos de clase con los que se construyen los artefactos de prueba. Valen 1,
#: que es la identidad, para que los tests que no estudian la corrección no se
#: vean alterados por ella. La corrección propiamente se prueba aparte, con
#: pesos reales.
PESOS = np.ones(len(LABEL_NAMES), dtype=np.float64)


def _stats(desvio: float = 1.0, media: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    return (np.full(N_FEATURES, media, dtype=np.float32),
            np.full(N_FEATURES, desvio, dtype=np.float32))


def _artefacto(model_config: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    torch.manual_seed(SEED)
    media, desvio = _stats(**kwargs.pop("stats_kwargs", {}))
    return build_artifact(
        NasdaqMLP(**(model_config or dict(DEFAULT_MODEL_CONFIG))),
        model_type="mlp",
        model_config=model_config or dict(DEFAULT_MODEL_CONFIG),
        stats_mean=media,
        stats_std=desvio,
        stable_band_pct=0.10,
        trained_at_ms=1791316500000,
        # Por defecto pesos unitarios, que es la identidad: los tests que no
        # estudian la corrección no deben verse afectados por ella.
        class_weights=kwargs.pop("class_weights", PESOS),
        training_device="cpu",
    )


#: Los pesos que dio el entrenamiento real sobre el dataset completo.
PESOS_REALES = np.array([1.311, 0.397, 1.292], dtype=np.float64)


@pytest.fixture()
def modelo_ponderado(tmp_path: Path) -> LocalPredictiveModel:
    """Misma red que `modelo`, pero con los pesos de clase del entrenamiento real.

    `torch.manual_seed(SEED)` dentro de `_artefacto` garantiza que los pesos de
    la red sean idénticos: así la única diferencia entre los dos modelos es la
    corrección de prior.
    """
    ruta = save_artifact(_artefacto(class_weights=PESOS_REALES), tmp_path / "ponderado.pt")
    return LocalPredictiveModel(str(ruta))


@pytest.fixture()
def modelo(tmp_path: Path) -> LocalPredictiveModel:
    """Un modelo guardado con stats triviales (media 0, desviación 1)."""
    ruta = save_artifact(_artefacto(), tmp_path / "modelo.pt")
    return LocalPredictiveModel(str(ruta))


@pytest.fixture()
def ventana() -> np.ndarray:
    return np.random.RandomState(SEED).randn(WINDOW, N_FEATURES).astype(np.float32)


# ---------------------------------------------------------------------------
# Arquitectura
# ---------------------------------------------------------------------------

class TestArquitectura:
    def test_mlp_devuelve_tres_logits(self) -> None:
        torch.manual_seed(SEED)
        salida = NasdaqMLP()(torch.zeros(WINDOW * N_FEATURES))
        assert salida.shape == (3,)

    def test_mlp_con_la_configuracion_por_defecto_del_plan(self) -> None:
        # Plan §2 congelado: 120 → 64 → 32 → 3 con dropout 0.20.
        assert DEFAULT_MODEL_CONFIG == {
            "input_dim": WINDOW * N_FEATURES, "hidden1": 64, "hidden2": 32,
            "n_classes": 3, "dropout": 0.20,
        }

    def test_mlp_tiene_el_numero_de_parametros_declarado(self) -> None:
        # 9.923 parámetros, declarados en el plan §2 antes de entrenar.
        assert sum(p.numel() for p in NasdaqMLP().parameters()) == 9_923

    def test_mlp_rechaza_dropout_fuera_de_rango(self) -> None:
        with pytest.raises(ValueError):
            NasdaqMLP(dropout=1.0)
        with pytest.raises(ValueError):
            NasdaqMLP(dropout=-0.1)

    def test_lineal_es_solo_una_capa(self) -> None:
        # Baseline 4 (plan §3): una sola capa, sin no-linealidad ni dropout.
        lineal = NasdaqLinear()
        assert isinstance(lineal.fc, torch.nn.Linear)
        assert sum(p.numel() for p in lineal.parameters()) == (WINDOW * N_FEATURES + 1) * 3
        assert lineal(torch.zeros(WINDOW * N_FEATURES)).shape == (3,)

    def test_make_model_reconstruye_desde_la_config_guardada(self) -> None:
        modelo = make_model("mlp", dict(DEFAULT_MODEL_CONFIG))
        assert isinstance(modelo, NasdaqMLP)
        assert isinstance(make_model("linear", {"input_dim": 120, "n_classes": 3}), NasdaqLinear)
        with pytest.raises(ValueError):
            make_model("transformer", {})

    def test_config_lineal_solo_trae_lo_que_el_lineal_acepta(self) -> None:
        """`make_model` hace `NasdaqLinear(**config)`: un `hidden` extra reventaría.

        La corrección que hace `arquitectura_activa` de `linear` a `mlp` es
        cambiar de artefacto, no de código; para que eso funcione cada
        arquitectura debe llevar la configuración que su constructor acepta.
        """
        assert DEFAULT_LINEAR_CONFIG == {"input_dim": WINDOW * N_FEATURES, "n_classes": 3}
        lineal = make_model("linear", dict(DEFAULT_LINEAR_CONFIG))
        assert sum(p.numel() for p in lineal.parameters()) == (WINDOW * N_FEATURES + 1) * 3


# ---------------------------------------------------------------------------
# Artefacto
# ---------------------------------------------------------------------------

class TestArtefacto:
    def test_guarda_y_carga_roundtrip(self, tmp_path: Path) -> None:
        artefacto = _artefacto()
        ruta = save_artifact(artefacto, tmp_path / "sub" / "carpeta" / "m.pt")
        assert ruta.is_file()  # crea los directorios que faltaban
        cargado = load_artifact(ruta)
        assert cargado["feature_schema_version"] == FEATURE_SCHEMA_VERSION
        assert cargado["artifact_format"] == ARTIFACT_FORMAT_VERSION
        assert cargado["window"] == WINDOW and cargado["horizon"] == HORIZON
        assert cargado["stable_band_pct"] == pytest.approx(0.10)
        assert cargado["feature_names"] == list(FEATURE_NAMES)

    def test_carga_solo_tipos_basicos_y_tensores(self, tmp_path: Path) -> None:
        # `build_artifact` debe limpiar los tipos de numpy, porque si el
        # artefacto los contuviera, `weights_only=True` rechazaría la carga en
        # M1. Este test es la red de seguridad de esa limpieza.
        artefacto = build_artifact(
            NasdaqMLP(), model_type="mlp", model_config=dict(DEFAULT_MODEL_CONFIG),
            stats_mean=np.zeros(N_FEATURES, np.float32),
            stats_std=np.ones(N_FEATURES, np.float32),
            stable_band_pct=0.10, trained_at_ms=0, training_device="cpu", class_weights=PESOS,
            metrics={"macro_f1": np.float32(0.5), "n": np.int64(3),
                     "por_tramo": {"[0.8,1.0)": {"n": np.int64(7)}}},
        )
        ruta = save_artifact(artefacto, tmp_path / "m.pt")
        cargado = load_artifact(ruta)
        assert cargado["metrics"] == {
            "macro_f1": 0.5, "n": 3, "por_tramo": {"[0.8,1.0)": {"n": 7}},
        }

    def test_los_estadisticos_vanian_como_lista_de_float(self, tmp_path: Path) -> None:
        media, desvio = _stats(desvio=2.5, media=1.5)
        artefacto = build_artifact(
            NasdaqMLP(), model_type="mlp", model_config=dict(DEFAULT_MODEL_CONFIG),
            stats_mean=media, stats_std=desvio, stable_band_pct=0.10,
            trained_at_ms=0, training_device="cpu", class_weights=PESOS,
        )
        cargado = load_artifact(save_artifact(artefacto, tmp_path / "m.pt"))
        assert cargado["stats_mean"] == pytest.approx([1.5] * N_FEATURES)
        assert cargado["stats_std"] == pytest.approx([2.5] * N_FEATURES)
        assert all(isinstance(v, float) for v in cargado["stats_mean"])

    def test_los_pesos_sobreviven_al_roundtrip(self, tmp_path: Path) -> None:
        torch.manual_seed(SEED)
        original = NasdaqMLP()
        original.eval()  # dropout apagado: sin esto la comparación sería aleatoria
        artefacto = build_artifact(
            original, model_type="mlp", model_config=dict(DEFAULT_MODEL_CONFIG),
            stats_mean=np.zeros(N_FEATURES, np.float32),
            stats_std=np.ones(N_FEATURES, np.float32),
            stable_band_pct=0.10, trained_at_ms=0, training_device="cpu", class_weights=PESOS,
        )
        ruta = save_artifact(artefacto, tmp_path / "m.pt")
        reconstruido = make_model("mlp", dict(load_artifact(ruta)["model_config"]))
        reconstruido.load_state_dict(load_artifact(ruta)["state_dict"])
        reconstruido.eval()
        x = torch.randn(WINDOW * N_FEATURES)
        assert torch.allclose(original(x), reconstruido(x), atol=1e-6)

    def test_archivo_inexistente_levanta_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_artifact(tmp_path / "no_existe.pt")

    def test_rechaza_estadisticos_de_forma_erronea(self) -> None:
        with pytest.raises(ValueError, match="misma forma"):
            build_artifact(
                NasdaqMLP(), model_type="mlp", model_config=dict(DEFAULT_MODEL_CONFIG),
                stats_mean=np.zeros(N_FEATURES, np.float32),
                stats_std=np.ones(N_FEATURES - 1, np.float32),
                stable_band_pct=0.1, trained_at_ms=0, training_device="cpu", class_weights=PESOS,
            )

    def test_rechaza_estadisticos_nan(self) -> None:
        media, desvio = _stats()
        media[0] = np.nan
        with pytest.raises(ValueError, match="NaN"):
            build_artifact(
                NasdaqMLP(), model_type="mlp", model_config=dict(DEFAULT_MODEL_CONFIG),
                stats_mean=media, stats_std=desvio,
                stable_band_pct=0.1, trained_at_ms=0, training_device="cpu", class_weights=PESOS,
            )

    def test_rechaza_desviacion_cero(self) -> None:
        # Una desviación a cero sería división por cero dentro de la inferencia.
        media, desvio = _stats()
        desvio[3] = 0.0
        with pytest.raises(ValueError, match="división por cero"):
            build_artifact(
                NasdaqMLP(), model_type="mlp", model_config=dict(DEFAULT_MODEL_CONFIG),
                stats_mean=media, stats_std=desvio,
                stable_band_pct=0.1, trained_at_ms=0, training_device="cpu", class_weights=PESOS,
            )

    def test_rechaza_model_type_desconocido(self) -> None:
        media, desvio = _stats()
        with pytest.raises(ValueError, match="model_type"):
            build_artifact(
                NasdaqMLP(), model_type="transformer", model_config=dict(DEFAULT_MODEL_CONFIG),
                stats_mean=media, stats_std=desvio,
                stable_band_pct=0.1, trained_at_ms=0, training_device="cpu", class_weights=PESOS,
            )

    def test_es_portable_entre_cpu_y_gpu(self, tmp_path: Path) -> None:
        """El artefacto se guarda con `map_location`: carga en el device que toque.

        En este banco no hay CUDA, así que lo que se verifica es que la carga
        funciona declarando un device explícito, que es el mecanismo que permite
        que un modelo entrenado en CPU (M2) se cargue en la GPU de M1.
        """
        ruta = save_artifact(_artefacto(), tmp_path / "m.pt")
        for device in ("cpu", torch.device("cpu")):
            assert load_artifact(ruta, map_location=device)["feature_names"] == list(FEATURE_NAMES)

    def test_los_pesos_de_clase_viajan_y_sobreviven(self, tmp_path: Path) -> None:
        artefacto = _artefacto(class_weights=PESOS_REALES)
        cargado = load_artifact(save_artifact(artefacto, tmp_path / "m.pt"))
        assert cargado["class_weights"] == pytest.approx(PESOS_REALES.tolist())
        assert all(isinstance(v, float) for v in cargado["class_weights"])

    def test_rechaza_un_vector_de_pesos_incompleto(self) -> None:
        media, desvio = _stats()
        with pytest.raises(ValueError, match="3 elementos"):
            build_artifact(
                NasdaqMLP(), model_type="mlp", model_config=dict(DEFAULT_MODEL_CONFIG),
                stats_mean=media, stats_std=desvio, stable_band_pct=0.1,
                trained_at_ms=0, training_device="cpu",
                class_weights=np.array([1.0, 1.0]),
            )

    def test_rechaza_pesos_no_positivos(self) -> None:
        media, desvio = _stats()
        with pytest.raises(ValueError, match="positivos"):
            build_artifact(
                NasdaqMLP(), model_type="mlp", model_config=dict(DEFAULT_MODEL_CONFIG),
                stats_mean=media, stats_std=desvio, stable_band_pct=0.1,
                trained_at_ms=0, training_device="cpu",
                class_weights=np.array([1.0, 0.0, 1.0]),
            )

    def test_un_artefacto_herido_sin_pesos_no_se_puede_inferir(self, tmp_path: Path) -> None:
        """Preferible que falle ruidosamente a servir una probabilidad que mide otra cosa.

        Un artefacto sin pesos no sabe con qué pérdida se entrenó, y exponer la
        softmax cruda como si fuera la confianza sería entregar al RiskManager
        un número que no dice lo que dice.
        """
        artefacto = _artefacto()
        artefacto.pop("class_weights")
        ruta = save_artifact(artefacto, tmp_path / "herido.pt")
        with pytest.raises(ValueError, match="class_weights"):
            LocalPredictiveModel(str(ruta))


class TestVersionDeEsquema:
    def test_acepta_la_version_activa(self) -> None:
        check_schema_version({"feature_schema_version": FEATURE_SCHEMA_VERSION})

    def test_rechaza_cualquier_otra_version(self) -> None:
        with pytest.raises(SchemaVersionError, match="incompatible"):
            check_schema_version({"feature_schema_version": "0"})
        with pytest.raises(SchemaVersionError):
            check_schema_version({})  # sin declaración = no confiable

    def test_el_modelo_se_niega_a_cargar_un_esquema_ajeno(self, tmp_path: Path) -> None:
        artefacto = _artefacto()
        artefacto["feature_schema_version"] = "0"
        ruta = save_artifact(artefacto, tmp_path / "viejo.pt")
        with pytest.raises(SchemaVersionError, match="Reentrenar"):
            LocalPredictiveModel(str(ruta))

    def test_la_version_viaja_en_el_artefacto(self, tmp_path: Path) -> None:
        cargado = load_artifact(save_artifact(_artefacto(), tmp_path / "m.pt"))
        assert cargado["feature_schema_version"] == FEATURE_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Inferencia — contrato literal del SDD §5.3.2
# ---------------------------------------------------------------------------

class TestRunInference:
    def test_devuelve_la_tupla_literal_del_sdd(self, modelo: LocalPredictiveModel, ventana: np.ndarray) -> None:
        tendencia, confianza, rationale = modelo.run_inference(ventana)
        assert tendencia in ("UP", "DOWN", "STABLE")
        assert isinstance(confianza, float)
        assert 0.0 <= confianza <= 1.0
        assert isinstance(rationale, str) and rationale.strip()

    def test_firmas_de_la_clase_son_literales_al_sdd(self) -> None:
        # `from __future__ import annotations` deja las anotaciones como texto.
        assert LocalPredictiveModel.__init__.__annotations__["model_path"] == "str"
        assert LocalPredictiveModel.run_inference.__annotations__["return"] == "tuple[str, float, str]"
        assert LocalPredictiveModel.load_model.__annotations__["return"] == "nn.Module"

    def test_el_modelo_queda_en_eval_dropout_apagado(self, modelo: LocalPredictiveModel) -> None:
        # Con dropout activo la inferencia no sería determinista, y una
        # predicción que cambia al repetirse no es auditable.
        assert modelo.model.training is False

    def test_selecciona_cuda_si_esta_disponible(self, modelo: LocalPredictiveModel) -> None:
        esperado = "cuda" if torch.cuda.is_available() else "cpu"
        assert modelo.device == esperado

    def test_la_inferencia_es_determinista(self, modelo: LocalPredictiveModel, ventana: np.ndarray) -> None:
        assert modelo.run_inference(ventana) == modelo.run_inference(ventana)

    def test_predict_proba_representa_una_distribucion(self, modelo: LocalPredictiveModel, ventana: np.ndarray) -> None:
        probs = modelo.predict_proba(ventana)
        assert probs.shape == (1, 3)
        assert probs.sum() == pytest.approx(1.0, abs=1e-5)
        assert (probs >= 0).all()

    def test_la_confianza_es_la_proba_de_la_clase_ganadora(self, modelo: LocalPredictiveModel, ventana: np.ndarray) -> None:
        # Con pesos unitarios la corrección es la identidad, así que aquí sí
        # coincide con la softmax. Lo importante está en el test de abajo.
        probs = modelo.predict_proba(ventana)[0]
        _, confianza, _ = modelo.run_inference(ventana)
        assert confianza == pytest.approx(float(probs.max()), abs=1e-6)

    def test_la_decision_no_depende_de_los_pesos_pero_la_confianza_si(
            self, modelo_ponderado, modelo: LocalPredictiveModel, ventana: np.ndarray) -> None:
        """El contrato completo: qué decide la red y qué número se expone.

        `modelo_ponderado` y `modelo` cargan **los mismos pesos de la red**;
        solo difieren en los pesos de clase. Por eso:
        - la tendencia (argmax de la softmax) sale idéntica: los pesos no
          tocan la decisión;
        - la confianza cambia: es el posterior corregido de esa clase.
        Si la primera fallara, se estaría mezclando la corrección con la
        decisión; si la segunda, la corrección no se aplicaría.
        """
        tendencia_ponderada, conf_ponderada, _ = modelo_ponderado.run_inference(ventana)
        tendencia_unitaria, conf_unitaria, _ = modelo.run_inference(ventana)
        assert tendencia_ponderada == tendencia_unitaria
        assert not np.isclose(conf_ponderada, conf_unitaria, atol=1e-4)
        # La de pesos unitarios es, por definición, la softmax cruda.
        assert conf_unitaria == pytest.approx(
            float(modelo.predict_proba(ventana)[0].max()), abs=1e-6)

    def test_no_modifica_el_tensor_que_recibe(self, modelo: LocalPredictiveModel, ventana: np.ndarray) -> None:
        original = ventana.copy()
        modelo.run_inference(ventana)
        assert np.array_equal(ventana, original)

    def test_no_evalua_el_umbral_del_75_por_ciento(self, modelo: LocalPredictiveModel, ventana: np.ndarray) -> None:
        """RF-304 deja el umbral al RiskManager (Sprint 4); el modelo solo informa."""
        _, confianza, _ = modelo.run_inference(ventana)
        assert confianza >= 0.0  # devuelve el número, no decide nada con él
        assert "75" not in (modelo.run_inference(ventana)[2] or "")


class TestNormalizacion:
    def test_prepare_aplica_los_estadisticos_del_artefacto(self, tmp_path: Path) -> None:
        media = np.full(N_FEATURES, 3.0, dtype=np.float32)
        desvio = np.full(N_FEATURES, 2.0, dtype=np.float32)
        artefacto = build_artifact(
            NasdaqMLP(), model_type="mlp", model_config=dict(DEFAULT_MODEL_CONFIG),
            stats_mean=media, stats_std=desvio, stable_band_pct=0.10,
            trained_at_ms=0, training_device="cpu", class_weights=PESOS,
        )
        instancia = LocalPredictiveModel(str(save_artifact(artefacto, tmp_path / "m.pt")))
        entrada = np.random.RandomState(SEED).randn(WINDOW, N_FEATURES).astype(np.float32)
        resultado = instancia._prepare(entrada).cpu().numpy()
        assert resultado.shape == (1, WINDOW * N_FEATURES)
        esperado = ((entrada - media) / desvio).reshape(1, -1)
        assert np.allclose(resultado, esperado, atol=1e-6)

    def test_dos_estadisticos_distintos_cambian_la_prediccion(self, tmp_path: Path) -> None:
        """Si la normalización no se aplicara, daría exactamente lo mismo."""
        rutas = []
        for i, stats in enumerate([(0.0, 1.0), (100.0, 50.0)]):
            media = np.full(N_FEATURES, stats[0], dtype=np.float32)
            desvio = np.full(N_FEATURES, stats[1], dtype=np.float32)
            artefacto = build_artifact(
                NasdaqMLP(), model_type="mlp", model_config=dict(DEFAULT_MODEL_CONFIG),
                stats_mean=media, stats_std=desvio, stable_band_pct=0.10,
                trained_at_ms=0, training_device="cpu", class_weights=PESOS,
            )
            rutas.append(save_artifact(artefacto, tmp_path / f"m{i}.pt"))
        x = np.full((WINDOW, N_FEATURES), 50.0, dtype=np.float32)
        probs = [LocalPredictiveModel(str(r)).predict_proba(x)[0] for r in rutas]
        assert not np.allclose(probs[0], probs[1], atol=1e-3)

    def test_acepta_las_tres_formas_de_entrada(self, modelo: LocalPredictiveModel) -> None:
        x = np.random.RandomState(SEED).randn(WINDOW, N_FEATURES).astype(np.float32)
        assert modelo.run_inference(x)[0] in ("UP", "DOWN", "STABLE")
        assert modelo.run_inference(x[None, ...])[0] in ("UP", "DOWN", "STABLE")
        assert modelo.run_inference(x.reshape(-1))[0] in ("UP", "DOWN", "STABLE")

    def test_rechaza_una_ventana_de_mal_tamano(self, modelo: LocalPredictiveModel) -> None:
        with pytest.raises(ValueError, match="12 velas"):
            modelo.run_inference(np.zeros((WINDOW - 1, N_FEATURES), np.float32))

    def test_rechaza_una_ventana_con_menos_features(self, modelo: LocalPredictiveModel) -> None:
        with pytest.raises(ValueError):
            modelo.run_inference(np.zeros((WINDOW, N_FEATURES - 1), np.float32))

    def test_rechaza_un_vector_aplano_que_no_es_multiplo(self, modelo: LocalPredictiveModel) -> None:
        with pytest.raises(ValueError, match="múltiplo"):
            modelo.run_inference(np.zeros(WINDOW * N_FEATURES + 1, np.float32))

    def test_rechaza_una_forma_no_soportada(self, modelo: LocalPredictiveModel) -> None:
        with pytest.raises(ValueError, match="no soportada"):
            modelo.run_inference(np.zeros((2, 3, 4, 5), np.float32))


# ---------------------------------------------------------------------------
# XAI
# ---------------------------------------------------------------------------

class TestAtribucion:
    def test_atribucion_con_la_forma_de_la_ventana(self, modelo: LocalPredictiveModel) -> None:
        x = modelo._prepare(np.random.RandomState(SEED).randn(WINDOW, N_FEATURES).astype(np.float32))
        attr = gradient_x_input(modelo.model, x, 0)
        assert attr.shape == (1, WINDOW, N_FEATURES)
        assert np.isfinite(attr).all()

    def test_atribucion_sobre_entrada_aplana(self, modelo: LocalPredictiveModel) -> None:
        x = modelo._prepare(np.random.RandomState(SEED).randn(WINDOW, N_FEATURES).astype(np.float32))
        assert gradient_x_input(modelo.model, x[0], 1).shape == (1, WINDOW, N_FEATURES)

    def test_atribucion_es_el_producto_gradiente_por_entrada(self) -> None:
        # Modelo trivial: salida = suma de las entradas. El gradiente es 1 en
        # todas partes, así que la atribución debe ser exactamente la entrada.
        class Suma(torch.nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.param = torch.nn.Parameter(torch.ones(1))

            def forward(self, entrada: torch.Tensor) -> torch.Tensor:
                return (entrada * self.param).sum(dim=1, keepdim=True).expand(-1, 3)

        entrada = torch.randn(1, WINDOW * N_FEATURES)
        attr = gradient_x_input(Suma(), entrada, 0)
        assert np.allclose(attr[0].reshape(-1), entrada[0].numpy(), atol=1e-6)

    def test_rechaza_entradas_de_mal_tamano(self, modelo: LocalPredictiveModel) -> None:
        with pytest.raises(ValueError, match="aplanadas"):
            gradient_x_input(modelo.model, torch.zeros(1, 7), 0)
        with pytest.raises(ValueError, match="aplanada"):
            gradient_x_input(modelo.model, torch.zeros(1, 2, 3, 4), 0)

    def test_no_deja_gradientes_colgados_del_tensor(self, modelo: LocalPredictiveModel, ventana: np.ndarray) -> None:
        x = modelo._prepare(ventana)
        before = x.grad
        gradient_x_input(modelo.model, x, 0)
        assert x.grad is before  # el clon interno no toca el tensor del llamador


class TestGrupos:
    def test_los_grupos_cubren_todas_las_features_exactamente_una_vez(self) -> None:
        catalogadas = [f for features in FEATURE_GROUPS.values() for f in features]
        assert sorted(catalogadas) == sorted(FEATURE_NAMES)
        assert len(catalogadas) == len(set(catalogadas))

    def test_group_of_devuelve_el_grupo(self) -> None:
        assert group_of("close_pos") == "presión direccional"
        assert group_of("vol_rel") == "volumen"

    def test_group_of_rechaza_features_sin_catalogar(self) -> None:
        with pytest.raises(KeyError):
            group_of("precio_del_bitcoin")

    def test_toda_feature_tiene_etiqueta_financiera(self) -> None:
        from src.ai.xai import FEATURE_LABELS, label_of
        assert sorted(FEATURE_LABELS) == sorted(FEATURE_NAMES)
        for nombre in FEATURE_NAMES:
            etiqueta = label_of(nombre)
            assert etiqueta.startswith("el ") or etiqueta.startswith("la ")
            assert "_" not in etiqueta
        with pytest.raises(KeyError):
            label_of("precio_del_bitcoin")

    def test_los_porcentajes_de_grupo_suman_cien(self) -> None:
        attr = np.random.RandomState(SEED).randn(WINDOW, N_FEATURES)
        grupos = attribution_by_group(attr)
        assert set(grupos) == set(FEATURE_GROUPS)
        assert sum(grupos.values()) == pytest.approx(100.0)

    def test_los_porcentajes_de_posicion_suman_cien(self) -> None:
        posiciones = attribution_by_position(np.random.RandomState(SEED).randn(WINDOW, N_FEATURES))
        assert posiciones.shape == (WINDOW,)
        assert posiciones.sum() == pytest.approx(100.0)

    def test_atribucion_cero_no_produce_nan(self) -> None:
        # Red muerta o entrada en cero: un NaN en un informe de auditoría es
        # peor que un reparto uniforme declarado.
        attr = np.zeros((WINDOW, N_FEATURES))
        assert sum(attribution_by_group(attr).values()) == pytest.approx(100.0)
        assert attribution_by_position(attr).sum() == pytest.approx(100.0)

    def test_atribucion_con_mal_numero_de_features_rechaza(self) -> None:
        with pytest.raises(ValueError, match="features"):
            attribution_by_group(np.zeros((WINDOW, N_FEATURES - 1)))


class TestRationale:
    def test_es_lenguaje_financiero_y_no_nombres_de_variable(self) -> None:
        attr = np.random.RandomState(SEED).randn(WINDOW, N_FEATURES)
        texto = build_rationale(attr, np.array([0.2, 0.3, 0.5]), 2)
        for nombre_variable in FEATURE_NAMES:
            assert nombre_variable not in texto, f"{nombre_variable} es código, no lenguaje financiero"
        for grupo in FEATURE_GROUPS:
            assert grupo in texto

    def test_incluye_la_advertencia_de_que_no_es_causalidad(self) -> None:
        # RF-303: una atribución de decisión no es una justificación de mercado.
        attr = np.random.RandomState(SEED).randn(WINDOW, N_FEATURES)
        texto = build_rationale(attr, np.array([0.2, 0.3, 0.5]), 2)
        assert "no es una causalidad de mercado" in texto
        assert "RF-303" in texto

    def test_declara_las_tres_probabilidades_y_la_decision(self) -> None:
        attr = np.random.RandomState(SEED).randn(WINDOW, N_FEATURES)
        probs = np.array([0.2, 0.3, 0.5])
        texto = build_rationale(attr, probs, 2)
        for nombre in ("DOWN", "STABLE", "UP"):
            assert nombre in texto
        assert "Decisión: UP" in texto
        assert "50,0 %" in texto  # formato español

    def test_usa_coma_decimal_en_los_porcentajes(self) -> None:
        # Interfaz en español neutro (convención 5.5): coma decimal.
        attr = np.random.RandomState(SEED).randn(WINDOW, N_FEATURES)
        texto = build_rationale(attr, np.array([0.2, 0.3, 0.5]), 2)
        porcentajes = re.findall(r"\d+(?:[.,]\d+)? %", texto)
        assert porcentajes, "el rationale debería citar porcentajes"
        assert all("," in p for p in porcentajes), porcentajes

    def test_el_rationale_producido_por_run_inference_es_usable(self, modelo: LocalPredictiveModel, ventana: np.ndarray) -> None:
        tendencia, _, rationale = modelo.run_inference(ventana)
        assert tendencia in rationale
        assert len(rationale) > 200
        assert f"de {WINDOW} velas" in rationale

    def test_el_rationale_cita_grupos_que_suman_cien(self) -> None:
        attr = np.random.RandomState(SEED).randn(WINDOW, N_FEATURES)
        texto = build_rationale(attr, np.array([0.2, 0.3, 0.5]), 2)
        for grupo in FEATURE_GROUPS:
            assert f"«{grupo}»" in texto
