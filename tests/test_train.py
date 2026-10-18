"""Pruebas del CLI de entrenamiento (`src/ai/train.py`).

Lo que se protege aquí es el informe, no la red. Un informe que compare las
predicciones del modelo contra sí mismo daría precisiones de 1,0 y un macro-F1
perfecto por tercios, y eso ya pasó: `construir_informe` usaba
`pred_mlp["test"]` como si fuera `(reales, confianza)` y en realidad era
`(predichas, confianza)`. El test
`test_informe_no_confunde_reales_con_predichos` es la regresión de ese fallo.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import torch

from src.ai.dataset import Dataset, DatasetConfig, build_dataset
from src.ai.metrics import pesos_de_clase
from src.ai.model import LocalPredictiveModel
from src.ai.train import (
    Entrenamiento,
    _marcas_de_fecha,
    _parsear_argumentos,
    construir_informe,
    entrenar_lineal,
    entrenar_mlp,
    fijar_semilla,
    predecir,
)

MINUTO = 60_000
VELA = 5 * MINUTO
T0 = 1791331800000
SESION = 78 * VELA + 18 * 60 * MINUTO


def _sesion(index: int, n: int = 78) -> list:
    from src.ai.features import Candle
    rng = np.random.RandomState(index)
    inicio = T0 + index * SESION
    velas, precio = [], 100.0 + index
    for i in range(n):
        apertura = precio
        cierre = apertura * (1.0 + rng.randn() * 0.0015)
        velas.append(Candle(
            timestamp=inicio + i * VELA, open=apertura,
            high=max(apertura, cierre) * 1.001, low=min(apertura, cierre) * 0.999,
            close=cierre, volume=900.0 + abs(rng.randn()) * 300.0,
        ))
        precio = cierre
    return velas


@pytest.fixture(scope="module")
def ds() -> Dataset:
    """Dataset sintético de ruido puro: aquí ninguna señal real existe."""
    velas = [c for i in range(24) for c in _sesion(i)]
    return build_dataset(velas, DatasetConfig())


@pytest.fixture(scope="module")
def cfg() -> Entrenamiento:
    return Entrenamiento(max_epochs=8, paciencia=4)


@pytest.fixture(scope="module")
def pesos(ds: Dataset) -> np.ndarray:
    return pesos_de_clase(np.asarray([s.y for s in ds.train], dtype=np.int64))


@pytest.fixture(scope="module")
def modelos(ds: Dataset, cfg: Entrenamiento, pesos: np.ndarray):
    """Entrena los dos una sola vez por módulo: `entrenar_*` devuelve
    `(modelo, historial, mejor_val_macro_f1)`, no solo el modelo."""
    mlp, _, _ = entrenar_mlp(ds, cfg, pesos, "cpu")
    lineal, _, _ = entrenar_lineal(ds, cfg, pesos, "cpu")
    return mlp, lineal


@pytest.fixture(scope="module")
def informe(ds: Dataset, cfg: Entrenamiento, pesos: np.ndarray, modelos) -> dict:
    """Informe completo una sola vez por módulo: es el mismo cálculo que hace el
    CLI al final del entrenamiento."""
    mlp, lineal = modelos
    return construir_informe(ds, mlp, lineal, cfg, pesos, "cpu", replicas=100)


# ---------------------------------------------------------------------------
# Entrenamiento
# ---------------------------------------------------------------------------

class TestEntrenamiento:
    def test_el_mlp_converge_en_pocas_epocas_sobre_ruido(self, ds, cfg, pesos) -> None:
        modelo, historial, mejor = entrenar_mlp(ds, cfg, pesos, "cpu")
        assert 1 <= len(historial) <= cfg.max_epochs
        assert 0.0 <= mejor <= 1.0
        assert modelo.training is False     # dropout apagado tras entrenar

    def test_early_stopping_no_rebasa_la_paciencia(self, ds, cfg, pesos) -> None:
        # Sobre ruido puro el val macro-F1 pronto deja de mejorar.
        _, historial, _ = entrenar_mlp(ds, cfg, pesos, "cpu")
        assert len(historial) <= cfg.max_epochs

    def test_el_lineal_usa_el_mismo_procedimiento(self, ds, cfg, pesos) -> None:
        modelo, historial, _ = entrenar_lineal(ds, cfg, pesos, "cpu")
        assert isinstance(modelo.fc, torch.nn.Linear)
        assert modelo.training is False
        assert 1 <= len(historial) <= cfg.max_epochs

    def test_entrenar_es_reproducible_con_la_misma_semilla(self, ds, cfg, pesos) -> None:
        a, _, _ = entrenar_mlp(ds, cfg, pesos, "cpu")
        b, _, _ = entrenar_mlp(ds, cfg, pesos, "cpu")
        for pa, pb in zip(a.parameters(), b.parameters()):
            assert torch.allclose(pa, pb)

    def test_fijar_semilla_hace_reproducible_la_inicializacion(self) -> None:
        fijar_semilla(1234)
        a = torch.rand(5)
        fijar_semilla(1234)
        b = torch.rand(5)
        assert torch.allclose(a, b)

    def test_rechaza_si_algun_corte_esta_vacio(self, ds, pesos) -> None:
        vacio = Dataset(
            train=ds.train, val=[], test=ds.test, stats=ds.stats, config=ds.config,
            session_start_idx=ds.session_start_idx, descartadas_cortas=0,
        )
        with pytest.raises(ValueError, match="cortes vacíos"):
            entrenar_mlp(vacio, Entrenamiento(max_epochs=1), pesos, "cpu")

    def test_no_modifica_el_dataset(self, ds, cfg, pesos) -> None:
        antes = len(ds.train)
        entrenar_lineal(ds, cfg, pesos, "cpu")
        assert len(ds.train) == antes


class TestPredecir:
    def test_devuelve_predicciones_y_confianza_alineadas(self, ds, modelos) -> None:
        modelo, _ = modelos
        pred, conf = predecir(modelo, ds, "test", "cpu")
        assert pred.shape == conf.shape == (len(ds.test),)
        assert set(np.unique(pred)).issubset({0, 1, 2})
        assert ((conf >= 1 / 3 - 1e-6) & (conf <= 1.0)).all()

    def test_la_confianza_es_la_proba_de_la_clase_ganadora(self, ds, modelos) -> None:
        modelo, _ = modelos
        pred, conf = predecir(modelo, ds, "val", "cpu")
        X, _ = ds.matrix("val")
        with torch.no_grad():
            probs = torch.softmax(modelo(torch.from_numpy(
                X.reshape(len(X), -1).astype(np.float32))), dim=1).numpy()
        assert np.allclose(conf, probs.max(axis=1), atol=1e-5)
        assert np.array_equal(pred, probs.argmax(axis=1))

    def test_un_corte_vacio_devuelve_vectores_vacios(self, ds, modelos) -> None:
        modelo, _ = modelos
        vacio = Dataset(train=ds.train, val=[], test=ds.test, stats=ds.stats,
                        config=ds.config, session_start_idx=ds.session_start_idx,
                        descartadas_cortas=0)
        pred, conf = predecir(modelo, vacio, "val", "cpu")
        # No se compara con `==`: un `array` vacío no tiene valor de verdad.
        assert pred.shape == conf.shape == (0,)
        assert pred.dtype == np.int64 and conf.dtype == float


# ---------------------------------------------------------------------------
# Informe — regresión del fallo de reales vs predichos
# ---------------------------------------------------------------------------

class TestInforme:
    def test_informe_no_confunde_reales_con_predichos(self, informe) -> None:
        """Regresión del defecto que inflaba el informe entero.

        Si se comparara el modelo contra sí mismo, la precisión daría 1,0 y el
        macro-F1 por tercios sería perfecto en los tres.
        """
        for fila in informe["tercios_test"]:
            assert fila["macro_f1"] < 1.0, "el informe se está comparando consigo mismo"
            assert fila["accuracy"] < 1.0
        for tramo in informe["calibracion_test"]:
            if tramo["n"]:
                assert tramo["precision_observada"] < 1.0

    def test_sobre_ruido_el_modelo_no_supera_al_azar_demostrablemente(self, informe) -> None:
        """Un modelo sobre ruido puro no puede tener ventaja demostrada.

        Si saliera `ventaja_demostrada: True` aquí, algo del informe estaría
        midiendo contra un objetivo equivocado.
        """
        contra_azar = informe["comparaciones_test"]["azar_por_priors"]
        assert not contra_azar["ventaja_demostrada"]

    def test_el_ic_del_modelo_contiene_su_macro_f1_de_test(self, informe) -> None:
        mlp = informe["metricas"]["test"]["mlp"]["macro_f1"]
        inferior, superior = informe["ic_95_test_mlp"]
        assert inferior <= mlp <= superior

    def test_el_ic_de_la_diferencia_es_pareado_y_coherente(self, informe) -> None:
        for nombre, datos in informe["comparaciones_test"].items():
            assert datos["diferencia"] == pytest.approx(
                datos["macro_f1_modelo"] - datos["macro_f1_baseline"], abs=1e-9
            ), nombre
            inferior, superior = datos["ic_95_diferencia"]
            assert inferior <= superior

    def test_los_baselines_del_informe_coinciden_con_sus_metricas(self, informe) -> None:
        mlp = informe["metricas"]["test"]["mlp"]["macro_f1"]
        assert informe["baselines_test"]["lineal"]["macro_f1"] == pytest.approx(
            informe["metricas"]["test"]["lineal"]["macro_f1"]
        )
        assert mlp < 1.0

    def test_el_informe_declara_los_hiperparametros_del_plan(self, informe) -> None:
        h = informe["hiperparametros"]
        assert h["lr"] == 1e-3 and h["weight_decay"] == 1e-4
        assert h["batch_size"] == 256 and h["max_epochs"] == 8
        assert h["paciencia"] == 4 and h["dropout"] == 0.20

    def test_declara_que_el_dispositivo_fue_cpu_en_el_banco(self, informe) -> None:
        assert informe["modelo"]["dispositivo_entrenamiento"] == "cpu"
        assert informe["modelo"]["torch_version"] == str(torch.__version__)

    def test_reporta_los_tres_cortes_y_sus_distribuciones(self, informe) -> None:
        assert set(informe["metricas"]) == {"train", "val", "test"}
        for split in ("train", "val", "test"):
            assert set(informe["dataset"]["distribucion"][split]) == {"DOWN", "STABLE", "UP"}

    def test_declara_la_banda_fija(self, informe) -> None:
        assert informe["dataset"]["banda_stable_pct"] == 0.10

    def test_incluye_la_tabla_de_umbrales_y_la_calibracion(self, informe) -> None:
        n_test = informe["dataset"]["muestras"]["test"]
        assert len(informe["umbrales_test"]) == 3
        assert [f["umbral"] for f in informe["umbrales_test"]] == [0.85, 0.75, 0.65]
        # Los tramos de calibración reparten TODAS las muestras de test: si
        # faltara alguna, el tramo que se caiga ocultaría el peor de los casos.
        assert sum(f["n"] for f in informe["calibracion_test"]) == n_test

    def test_incluye_los_tercios_del_test(self, informe) -> None:
        n_test = informe["dataset"]["muestras"]["test"]
        assert len(informe["tercios_test"]) == 3
        assert sum(f["n"] for f in informe["tercios_test"]) == n_test

    def test_es_serializable_a_json(self, informe) -> None:
        assert json.loads(json.dumps(informe))

    def test_declara_los_pesos_de_clase(self, informe) -> None:
        pesos = informe["pesos_de_clase"]
        assert set(pesos) == {"DOWN", "STABLE", "UP"}
        assert abs(np.mean(list(pesos.values())) - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# Artefacto que produce el entrenamiento
# ---------------------------------------------------------------------------

class TestArtefactoDelEntrenamiento:
    def test_el_modelo_entrenado_sirve_para_inferir(self, ds, modelos, tmp_path: Path) -> None:
        from src.ai.model import DEFAULT_MODEL_CONFIG, build_artifact, save_artifact
        mlp, _ = modelos
        artefacto = build_artifact(
            mlp, model_type="mlp", model_config=dict(DEFAULT_MODEL_CONFIG),
            stats_mean=ds.stats.mean, stats_std=ds.stats.std,
            stable_band_pct=0.10, trained_at_ms=0, training_device="cpu",
            metrics={"macro_f1_test": 0.3},
        )
        ruta = save_artifact(artefacto, tmp_path / "m.pt")
        instancia = LocalPredictiveModel(str(ruta))
        tendencia, confianza, rationale = instancia.run_inference(ds.test[0].x)
        assert tendencia in ("UP", "DOWN", "STABLE")
        assert 0.0 <= confianza <= 1.0 and rationale

    def test_el_artefacto_normaliza_con_los_estadisticos_del_dataset(self, ds, modelos,
                                                                    tmp_path: Path) -> None:
        from src.ai.model import DEFAULT_MODEL_CONFIG, build_artifact, save_artifact
        mlp, _ = modelos
        artefacto = build_artifact(
            mlp, model_type="mlp", model_config=dict(DEFAULT_MODEL_CONFIG),
            stats_mean=ds.stats.mean, stats_std=ds.stats.std,
            stable_band_pct=0.10, trained_at_ms=0, training_device="cpu",
        )
        ruta = save_artifact(artefacto, tmp_path / "m.pt")
        instancia = LocalPredictiveModel(str(ruta))
        assert np.allclose(instancia.stats_mean, ds.stats.mean, atol=1e-6)
        assert np.allclose(instancia.stats_std, ds.stats.std, atol=1e-6)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestCli:
    def test_rechaza_menos_de_mil_replicas(self) -> None:
        from src.ai.train import main
        with pytest.raises(SystemExit, match="1.000"):
            main(["--replicas", "50"])

    def test_las_fechas_se_convierten_a_epoch_ms(self) -> None:
        inicio, fin = _marcas_de_fecha("1970-01-01", "1970-01-02")
        assert inicio == 0
        assert fin == 24 * 60 * 60 * 1000

    def test_sin_fechas_devuelve_none(self) -> None:
        assert _marcas_de_fecha(None, None) == (None, None)

    def test_los_argumentos_por_defecto_coinciden_con_el_plan(self) -> None:
        args = _parsear_argumentos([])
        assert args.semilla == 20261017
        assert args.epocas == 200
        assert args.paciencia == 20
        assert args.replicas == 2000
        assert args.salida == "models"

    def test_la_banda_stable_no_es_un_argumento(self) -> None:
        """La banda es constante del sistema; no se puede mover desde la CLI."""
        with pytest.raises(SystemExit):
            _parsear_argumentos(["--banda", "0.05"])
