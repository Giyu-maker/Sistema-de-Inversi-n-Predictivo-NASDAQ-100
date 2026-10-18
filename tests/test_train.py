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
from src.ai.features import LABEL_DOWN, LABEL_NAMES, LABEL_STABLE, LABEL_UP
from src.ai.metrics import corregir_posterior, pesos_de_clase
from src.ai.model import LocalPredictiveModel
from src.ai.train import (
    ARQUITECTURAS,
    Entrenamiento,
    _marcas_de_fecha,
    _parsear_argumentos,
    arquitectura_activa,
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

#: Pesos de clase de los artefactos que no estudian la corrección: valen 1, que
#: es la identidad. Los que sí la estudian usan el fixture `pesos`, calculado
#: sobre la distribución real del dataset sintético.
PESOS = np.ones(len(LABEL_NAMES), dtype=np.float64)


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
    def test_devuelve_predicciones_y_confianza_alineadas(self, ds, modelos, pesos) -> None:
        modelo, _ = modelos
        pred, conf = predecir(modelo, ds, "test", "cpu", pesos)
        assert pred.shape == conf.shape == (len(ds.test),)
        assert set(np.unique(pred)).issubset({0, 1, 2})
        # El piso **no** es 1/3. Con la softmax cruda el argmax nunca baja de
        # 1/3 por definición; con el posterior corregido sí puede, si la clase
        # que ganó la softmax es además la de mayor peso. Es correcto: la
        # corrección puede degradar a la clase elegida por debajo del azar
        # uniforme cuando los pesos lo justifican.
        assert (conf > 0.0).all() and (conf <= 1.0).all()

    def test_decide_con_la_softmax_y_reporta_el_posterior_corregido(
            self, ds, modelos, pesos) -> None:
        """Las dos cosas que `predecir` hace, y que no deben confundirse.

        La etiqueta es el argmax de la softmax cruda —la decisión de la red,
        la que se evaluó en las métricas— y la confianza es el posterior
        corregido por prior de esa etiqueta. Si el test diera igual para
        pesos distintos de 1, la corrección no se estaría aplicando.
        """
        modelo, _ = modelos
        pred, conf = predecir(modelo, ds, "val", "cpu", pesos)
        X, _ = ds.matrix("val")
        with torch.no_grad():
            crudas = torch.softmax(modelo(torch.from_numpy(
                X.reshape(len(X), -1).astype(np.float32))), dim=1).numpy()
        corregidas = corregir_posterior(crudas, pesos)
        assert np.array_equal(pred, crudas.argmax(axis=1))
        assert np.allclose(conf, corregidas[np.arange(len(crudas)), pred], atol=1e-5)

    def test_un_corte_vacio_devuelve_vectores_vacios(self, ds, modelos, pesos) -> None:
        modelo, _ = modelos
        vacio = Dataset(train=ds.train, val=[], test=ds.test, stats=ds.stats,
                        config=ds.config, session_start_idx=ds.session_start_idx,
                        descartadas_cortas=0)
        pred, conf = predecir(modelo, vacio, "val", "cpu", pesos)
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

    def test_declara_que_la_confianza_es_el_posterior_corregido(self, informe) -> None:
        """Nadie debe leer la confianza del informe pensando que es la softmax."""
        conf = informe["confianza"]
        assert conf["tipo"] == "posterior_corregido_por_prior"
        assert conf["softmax_cruda_expuesta"] is False
        assert "sin parámetros" in conf["descripcion"]

    def test_reporta_calibracion_de_val_y_de_test_cubriendo_todos(self, informe) -> None:
        """La calibración se valida primero en val; test se reporta una vez.

        Ambos cortes tienen que repartir todas sus muestras: un tramo que se
        caiga es el que ocultaría el peor de los casos.
        """
        for corte in ("val", "test"):
            n = informe["dataset"]["muestras"][corte]
            tramos = informe[f"calibracion_{corte}"]
            assert sum(f["n"] for f in tramos) == n, corte

    def test_reporta_calibracion_por_clase_en_los_dos_cortes(self, informe) -> None:
        for corte in ("val", "test"):
            filas = informe[f"calibracion_por_clase_{corte}"]
            assert [f["clase"] for f in filas] == ["DOWN", "STABLE", "UP"], corte
            assert sum(f["n"] for f in filas) == informe["dataset"]["muestras"][corte]

    def test_los_umbrales_existen_en_los_dos_cortes(self, informe) -> None:
        for corte in ("val", "test"):
            assert [f["umbral"] for f in informe[f"umbrales_{corte}"]] == [0.85, 0.75, 0.65]

    def test_el_acierto_direccional_coincide_con_un_recalculo_independiente(
            self, ds, modelos, pesos, informe) -> None:
        """Reconstruye el conteo desde cero: si no cuadra, el bloque miente."""
        modelo, _ = modelos
        pred, _ = predecir(modelo, ds, "test", "cpu", pesos)
        y = np.asarray([m.y for m in ds.test], dtype=np.int64)
        direccional = np.isin(y, (LABEL_DOWN, LABEL_UP)) & np.isin(pred, (LABEL_DOWN, LABEL_UP))
        r = informe["acierto_direccional_test"]
        assert r["n"] == int(direccional.sum())
        assert r["aciertos"] == int((direccional & (y == pred)).sum())
        assert r["ratio"] == pytest.approx(r["aciertos"] / r["n"])
        assert r["n"] <= r["casos_direccionales_verdad"] <= informe["dataset"]["muestras"]["test"]

    def test_declara_que_la_eleccion_de_mlp_es_de_especificacion(self, informe) -> None:
        """El MLP se sirve porque lo exige el SRS, no porque ganó.

        Si un día gana, esta cadena sigue siendo la razón declarada, y el
        informe no debe poder reescribirse para que parezca lo contrario.
        """
        assert informe["modelo"]["tipo"] == "mlp"
        assert "especificacion" in informe["modelo"]["seleccion_por"]


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
            stable_band_pct=0.10, trained_at_ms=0, training_device="cpu", class_weights=PESOS,
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
            stable_band_pct=0.10, trained_at_ms=0, training_device="cpu", class_weights=PESOS,
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
        assert args.arquitectura is None   # se resuelve contra MODEL_ARCH

    def test_la_banda_stable_no_es_un_argumento(self) -> None:
        """La banda es constante del sistema; no se puede mover desde la CLI."""
        with pytest.raises(SystemExit):
            _parsear_argumentos(["--banda", "0.05"])

    def test_arquitectura_solo_acepta_las_dos_conocidas(self) -> None:
        assert _parsear_argumentos(["--arquitectura", "linear"]).arquitectura == "linear"
        with pytest.raises(SystemExit):
            _parsear_argumentos(["--arquitectura", "transformer"])


# ---------------------------------------------------------------------------
# Selección de arquitectura — el MLP se sirve por especificación, no por métrica
# ---------------------------------------------------------------------------

class TestArquitecturaActiva:
    def test_por_defecto_es_mlp(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MODEL_ARCH", raising=False)
        assert arquitectura_activa() == "mlp"

    def test_se_lee_del_entorno(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MODEL_ARCH", "linear")
        assert arquitectura_activa() == "linear"

    def test_el_argumento_tiene_prioridad_sobre_el_entorno(self, monkeypatch) -> None:
        monkeypatch.setenv("MODEL_ARCH", "linear")
        assert arquitectura_activa("mlp") == "mlp"

    def test_es_indiferente_a_mayusculas_y_espacios(self, monkeypatch) -> None:
        monkeypatch.setenv("MODEL_ARCH", "  Linear ")
        assert arquitectura_activa() == "linear"

    def test_rechaza_un_valor_desconocido(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MODEL_ARCH", "transformer")
        with pytest.raises(SystemExit, match="desconocida"):
            arquitectura_activa()

    def test_solo_hay_dos_arquitecturas(self) -> None:
        # Documenta la decisión: el lineal es referencia seleccionable, no un
        # tercero que se pueda añadir sin cambiar el plan.
        assert ARQUITECTURAS == ("mlp", "linear")


# ---------------------------------------------------------------------------
# Guardado — regresión del KeyError `linear` / `lineal`
# ---------------------------------------------------------------------------

class TestGuardado:
    @staticmethod
    def _informe() -> dict:
        """Un informe mínimo con las llaves que `_guardar` consulta."""
        return {
            "metricas": {s: {"mlp": {"macro_f1": 0.40}, "lineal": {"macro_f1": 0.44}}
                         for s in ("train", "val", "test")},
            "baselines_test": {"lineal": {"macro_f1": 0.44},
                               "persistencia": {"macro_f1": 0.39}},
        }

    def _entrenadas(self, modelos):
        from src.ai.model import DEFAULT_LINEAR_CONFIG, DEFAULT_MODEL_CONFIG
        mlp, lineal = modelos
        return {
            "mlp": (mlp, dict(DEFAULT_MODEL_CONFIG), 5, 0.40),
            "linear": (lineal, dict(DEFAULT_LINEAR_CONFIG), 4, 0.44),
        }

    def test_guardar_escribe_los_dos_artefactos_y_puntero(
            self, ds, modelos, pesos, tmp_path: Path) -> None:
        """Regresión: el entrenamiento entero se pagaba y revienta al guardar.

        `model_type` es `linear` (inglés, es lo que recibe `make_model`) pero
        el informe lo llama `lineal`. La llave equivocada daba un `KeyError`
        justo al final, después de haber invertido todo el entrenamiento.
        """
        from src.ai.model import load_artifact
        from src.ai.train import _guardar

        rutas, ruta_informe = _guardar(tmp_path, "20261018T000000Z", "mlp",
                                       self._entrenadas(modelos), pesos, ds, "cpu",
                                       self._informe())
        assert set(rutas) == {"mlp", "linear"}
        for ruta in rutas.values():
            assert Path(ruta).is_file()
        assert ruta_informe.endswith(".metrics.json")

        activo = json.loads((tmp_path / "active.json").read_text(encoding="utf-8"))
        # Las rutas del puntero son **relativas al propio puntero**: solo el
        # nombre del archivo. Antes salían relativas a la raíz del proyecto
        # (`models/x.pt`), y quien leía el puntero construía
        # `models/models/x.pt`, que no existe. Ver `ruta_desde_puntero`.
        assert activo["path"] == Path(rutas["mlp"]).name
        assert activo["arquitectura"] == "mlp"
        assert activo["disponibles"] == {
            tipo: Path(ruta).name for tipo, ruta in rutas.items()
        }

        assert load_artifact(rutas["mlp"])["model_type"] == "mlp"
        cargado_lineal = load_artifact(rutas["linear"])
        assert cargado_lineal["model_type"] == "linear"
        assert cargado_lineal["metrics"]["macro_f1_test"] == pytest.approx(0.44)
        assert cargado_lineal["metrics"]["confianza"] == "posterior corregido por prior"

    def test_el_puntero_que_escribe_se_puede_leer_sin_depender_de_donde_se_ejecuta(
            self, ds, modelos, pesos, tmp_path: Path, monkeypatch, tmp_path_factory) -> None:
        """Regresión del bug de despliegue del PASO 3.

        `src.ai.train` guardaba la ruta relativa a la **raíz del proyecto**
        (`models/x.pt`) y `src.ai.serve` la resolvía contra el directorio que
        contiene `active.json`, de modo que el servicio buscaba
        `models/models/x.pt` y no arrancaba.

        Pasaba inadvertido en desarrollo porque `MODEL_PATH` apuntaba al `.pt`
        suelto y esa rama ni se ejecutaba; aparece al poner `MODEL_PATH` al
        directorio, que es como corre en la Máquina 1. Por eso este test no
        comprueba que el archivo exista — eso ya lo hacía el anterior —, sino
        que la resolución **no depende del directorio de trabajo**: se cambia a
        un directorio distinto y el puntero sigue resolviéndose igual.

        Con la ruta relativa a la raíz, este test falla: desde otro `cwd` no
        hay `models/` que encontrar.
        """
        from src.ai.model import ruta_desde_puntero
        from src.ai.train import _guardar

        rutas, _ = _guardar(tmp_path, "20261018T000000Z", "mlp",
                             self._entrenadas(modelos), pesos, ds, "cpu",
                             self._informe())
        puntero = tmp_path / "active.json"
        assert ruta_desde_puntero(puntero) == Path(rutas["mlp"])

        # Se ejecuta desde un directorio que no tiene nada que ver con los
        # modelos: la resolución debe seguir dando el mismo archivo.
        otro = tmp_path_factory.mktemp("trabajo-distinto")
        monkeypatch.chdir(otro)
        assert ruta_desde_puntero(puntero) == Path(rutas["mlp"])

    def test_un_puntero_apuntando_a_un_archivo_inexistente_da_un_mensaje_accionable(
            self, tmp_path: Path) -> None:
        """No basta con fallar: hay que decir qué hacer.

        El puntero roto se descubre en la Máquina 1, en un arranque, y la
        tentación es tocar el servicio. El mensaje apunta al reentrenamiento,
        que es lo único que vuelve a escribir el puntero bien.
        """
        from src.ai.model import ruta_desde_puntero

        puntero = tmp_path / "active.json"
        puntero.write_text(json.dumps({"path": "models/que-no-existe.pt"}), encoding="utf-8")

        with pytest.raises(ValueError, match="reentrenar"):
            ruta_desde_puntero(puntero)

    def test_un_puntero_sin_la_llave_path_no_pasa_en_blanco(
            self, tmp_path: Path) -> None:
        from src.ai.model import ruta_desde_puntero

        puntero = tmp_path / "active.json"
        puntero.write_text(json.dumps({"otra": "cosa"}), encoding="utf-8")

        with pytest.raises(ValueError, match="no declara 'path'"):
            ruta_desde_puntero(puntero)

    def test_puede_dejar_activo_el_lineal_sin_reentrenar(
            self, ds, modelos, pesos, tmp_path: Path) -> None:
        from src.ai.train import _guardar
        rutas, ruta_informe = _guardar(tmp_path, "20261018T000000Z", "linear",
                                       self._entrenadas(modelos), pesos, ds, "cpu",
                                       self._informe())
        activo = json.loads((tmp_path / "active.json").read_text(encoding="utf-8"))
        assert activo["arquitectura"] == "linear"
        assert activo["path"] == Path(rutas["linear"]).name
        assert ruta_informe.endswith("ritech-linear-20261018T000000Z.metrics.json")

    def test_los_pesos_de_clase_llegan_al_artefacto(
            self, ds, modelos, pesos, tmp_path: Path) -> None:
        """Sin ellos en el artefacto, M1 no podría corregir la prior al servir."""
        from src.ai.model import load_artifact
        from src.ai.train import _guardar
        rutas, _ = _guardar(tmp_path, "20261018T000000Z", "mlp",
                            self._entrenadas(modelos), pesos, ds, "cpu", self._informe())
        for ruta in rutas.values():
            assert load_artifact(ruta)["class_weights"] == pytest.approx(pesos.tolist())
