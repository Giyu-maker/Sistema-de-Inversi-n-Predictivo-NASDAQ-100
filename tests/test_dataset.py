"""Pruebas de la construcción del dataset (`src/ai/dataset.py`).

El dataset es donde se decide si el modelo sirve o no: un corte con fuga hace
que las métricas de val y test parezcan excelentes mientras que en producción
el modelo no vea nada parecido. Aquí se protegen las cuatro reglas del plan §3:
muestras dentro de una sesión, embargo entre cortes, estadísticos solo de train
y etiquetas trazables.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from src.ai.dataset import (
    Dataset,
    DatasetConfig,
    Sample,
    build_dataset,
    build_samples,
    distribution_at_band,
    distribution_of,
    load_candles,
    returns_of,
    split_with_embargo,
    train_only_stats,
)
from src.ai.features import (
    EMBARGO,
    FEATURE_NAMES,
    HORIZON,
    LABEL_DOWN,
    LABEL_NAMES,
    LABEL_STABLE,
    LABEL_UP,
    WINDOW,
    Candle,
    label_for,
    session_of,
)

N_FEATURES = len(FEATURE_NAMES)
MINUTO = 60_000
VELA = 5 * MINUTO
T0 = 1791331800000  # 06/10/2026 09:30 ET
NOCHE = 18 * 60 * MINUTO


#: Separación fija entre sesiones: una sesión completa + la noche. Es constante
#: para que una sesión sintética de `n` velas siempre empiece en su propia
#: franja, sea cual sea su longitud; si el paso dependiera de `n`, una sesión
#: corta caería dentro del horario de otra y `session_of` las fundiría.
SESION = 78 * VELA + NOCHE


def _sesion(index: int, n: int = 78, semilla: int = 0) -> list[Candle]:
    """Sesión sintética determinista de `n` velas, separada de la anterior por la noche."""
    rng = np.random.RandomState(semilla * 1000 + index)
    inicio = T0 + index * SESION
    velas: list[Candle] = []
    precio = 100.0 + index
    for i in range(n):
        apertura = precio
        cierre = apertura * (1.0 + rng.randn() * 0.0015)
        alto = max(apertura, cierre) * (1.0 + abs(rng.randn()) * 0.001)
        bajo = min(apertura, cierre) * (1.0 - abs(rng.randn()) * 0.001)
        velas.append(Candle(
            timestamp=inicio + i * VELA, open=apertura, high=alto, low=bajo,
            close=cierre, volume=900.0 + abs(rng.randn()) * 300.0,
        ))
        precio = cierre
    return velas


def _velas(n_sesiones: int, n_velas: int = 78) -> list[Candle]:
    return [c for i in range(n_sesiones) for c in _sesion(i, n_velas)]


def _dataset(n_sesiones: int = 60, n_velas: int = 78,
             config: DatasetConfig | None = None) -> Dataset:
    return build_dataset(_velas(n_sesiones, n_velas), config)


@pytest.fixture()
def ds() -> Dataset:
    return _dataset()


def _sesion_monotona(n: int, paso: float) -> list[Candle]:
    """Sesión con camino de precio estrictamente creciente (`paso` > 0) o decreciente."""
    velas: list[Candle] = []
    precio = 100.0
    for i in range(n):
        apertura = precio
        cierre = apertura * (1.0 + paso)
        velas.append(Candle(
            timestamp=T0 + i * VELA, open=apertura, high=max(apertura, cierre) * 1.001,
            low=min(apertura, cierre) * 0.999, close=cierre, volume=1000.0,
        ))
        precio = cierre
    return velas


# ---------------------------------------------------------------------------
# build_samples
# ---------------------------------------------------------------------------

class TestMuestras:
    def test_el_numero_de_muestras_es_el_de_las_sesiones_completas(self) -> None:
        muestras, _, _ = build_samples([_sesion(0)], DatasetConfig())
        assert len(muestras) == 78 - WINDOW - HORIZON + 1 == 64

    def test_cada_muestra_tiene_la_matriz_de_features_correcta(self) -> None:
        muestras, _, _ = build_samples([_sesion(0)], DatasetConfig())
        primera = muestras[0]
        assert primera.x.shape == (WINDOW, N_FEATURES)
        assert np.isfinite(primera.x).all()

    def test_las_ventanas_se_desplazan_una_sola_vela(self) -> None:
        """Muestras consecutivas comparten 11 de 12 velas: por eso el plan §4
        mide la incertidumbre con bootstrap por bloques de sesión."""
        muestras, _, _ = build_samples([_sesion(0)], DatasetConfig())
        assert all(
            b.window_start_idx == a.window_start_idx + 1
            for a, b in zip(muestras, muestras[1:])
        )

    def test_el_indice_de_inicio_de_sesion_es_acumulativo(self) -> None:
        sesiones = [_sesion(0, 78), _sesion(1, 78), _sesion(2, 78)]
        _, inicio, _ = build_samples(sesiones, DatasetConfig())
        assert inicio == [0, 78, 156]

    def test_las_sesiones_cortas_se_descartan_y_no_ocupan_hueco_del_corte(self) -> None:
        # Una sesión de 10 velas no alcanza para ventana + horizonte. No solo
        # no aporta muestras: tampoco puede ocupar un hueco del corte temporal,
        # o con pocas sesiones dejaría test vacío.
        sesiones = [_sesion(0, 78), _sesion(1, 10), _sesion(2, 78)]
        muestras, inicio, cortas = build_samples(sesiones, DatasetConfig())
        assert cortas == 1
        assert len(muestras) == 128
        assert len(inicio) == 2
        assert inicio == [0, 88]              # el hueco de las 10 velas queda vacío
        assert {s.session_idx for s in muestras} == {0, 1}

    def test_una_sesion_de_margen_produce_una_sola_muestra(self) -> None:
        muestras, _, _ = build_samples([_sesion(0, WINDOW + HORIZON)], DatasetConfig())
        assert len(muestras) == 1

    def test_sin_sesiones_devuelve_vacio(self) -> None:
        muestras, inicio, cortas = build_samples([], DatasetConfig())
        assert (muestras, inicio, cortas) == ([], [], 0)


class TestSinFugaEnLasMuestras:
    def test_ninguna_muestra_cruza_el_borde_de_sesion(self) -> None:
        """Regla 1 del plan: ni la ventana ni la etiqueta cruzan el cierre."""
        sesiones = [_sesion(0, 78), _sesion(1, 78), _sesion(2, 78)]
        muestras, inicio, _ = build_samples(sesiones, DatasetConfig())
        for muestra in muestras:
            fin_sesion = inicio[muestra.session_idx] + len(sesiones[muestra.session_idx])
            assert inicio[muestra.session_idx] <= muestra.window_start_idx
            assert muestra.label_end_idx < fin_sesion

    def test_ventana_y_horizonte_caben_enteros_en_su_sesion(self) -> None:
        """Comprobación por índice, no por tiempo: la muestra mide exactamente
        `WINDOW + HORIZON` velas y todas pertenecen a la misma sesión."""
        sesiones = [_sesion(0, 78), _sesion(1, 78)]
        muestras, inicio, _ = build_samples(sesiones, DatasetConfig())
        assert muestras
        for muestra in muestras:
            sesion = sesiones[muestra.session_idx]
            base = inicio[muestra.session_idx]
            ini = muestra.window_start_idx - base
            fin = muestra.label_end_idx - base
            assert fin - ini + 1 == WINDOW + HORIZON
            assert 0 <= ini <= fin < len(sesion)
            assert sesion[ini].timestamp <= sesion[fin].timestamp

    def test_la_etiqueta_mira_adelante_y_persistencia_hacia_atras(self) -> None:
        sesiones = [_sesion(0, 78)]
        muestras, _, _ = build_samples(sesiones, DatasetConfig())
        muestra = muestras[20]
        # El horizonte cae exactamente `HORIZON` velas después del cierre de la ventana.
        assert muestra.label_end_ts > muestra.window_end_ts
        assert muestra.label_end_ts - muestra.window_end_ts == HORIZON * VELA
        assert muestra.persistence_return_pct != 0.0

    def test_persistencia_es_el_retorno_de_las_velas_previas(self) -> None:
        """El baseline 3 se calcula con velas ANTERIORES al punto de predicción."""
        sesiones = [_sesion(0, 78)]
        muestras, inicio, _ = build_samples(sesiones, DatasetConfig())
        banda = DatasetConfig().stable_band_pct
        sesion = sesiones[0]
        for muestra in muestras[:5]:
            base = inicio[muestra.session_idx]
            fin_ventana = muestra.window_start_idx - base + WINDOW - 1
            esperado = (sesion[fin_ventana].close - sesion[fin_ventana - HORIZON].close) \
                / sesion[fin_ventana - HORIZON].close * 100.0
            assert muestra.persistence_return_pct == pytest.approx(esperado)
            assert muestra.persistence_y == label_for(esperado, banda)

    def test_persistencia_y_etiqueta_real_no_son_lo_mismo(self) -> None:
        sesiones = [_sesion(0, 78)]
        muestras, _, _ = build_samples(sesiones, DatasetConfig())
        distintas = sum(1 for m in muestras if m.persistence_y != m.y)
        assert distintas > 0, "la persistencia no puede coincidir siempre con la realidad"


class TestEtiquetasReales:
    def test_una_subida_fuerte_es_up(self) -> None:
        muestras, _, _ = build_samples([_sesion_monotona(78, +0.005)], DatasetConfig())
        assert muestras[0].y == LABEL_UP

    def test_una_bajada_fuerte_es_down(self) -> None:
        muestras, _, _ = build_samples([_sesion_monotona(78, -0.005)], DatasetConfig())
        assert muestras[0].y == LABEL_DOWN

    def test_un_precio_plano_es_stable(self) -> None:
        planas = [Candle(
            timestamp=T0 + i * VELA, open=100.0, high=100.05, low=99.95,
            close=100.0, volume=1000.0,
        ) for i in range(78)]
        muestras, _, _ = build_samples([planas], DatasetConfig())
        assert all(m.y == LABEL_STABLE for m in muestras)

    def test_el_retorno_de_la_muestra_coincide_con_su_etiqueta(self) -> None:
        banda = DatasetConfig().stable_band_pct
        muestras, _, _ = build_samples([_sesion(0, 78)], DatasetConfig())
        for muestra in muestras[:10]:
            if muestra.forward_return_pct > banda:
                assert muestra.y == LABEL_UP
            elif muestra.forward_return_pct < -banda:
                assert muestra.y == LABEL_DOWN
            else:
                assert muestra.y == LABEL_STABLE

    def test_las_tres_clases_pueden_aparecer_en_un_dataset_realista(self) -> None:
        dist = distribution_of(build_dataset(_velas(60)).train)
        assert set(dist) == set(LABEL_NAMES.values())
        assert all(v > 0 for v in dist.values())


# ---------------------------------------------------------------------------
# Cortes
# ---------------------------------------------------------------------------

def _velas_de(muestras: list[Sample]) -> set[int]:
    """Índices globales de vela tocados por un conjunto de muestras."""
    tocadas: set[int] = set()
    for m in muestras:
        tocadas.update(range(m.window_start_idx, m.label_end_idx + 1))
    return tocadas


class TestCortes:
    def test_los_cortes_son_70_15_15(self, ds: Dataset) -> None:
        total = len(ds.train) + len(ds.val) + len(ds.test)
        assert abs(len(ds.train) / total - 0.70) < 0.02
        assert abs(len(ds.val) / total - 0.15) < 0.02
        assert abs(len(ds.test) / total - 0.15) < 0.02

    def test_los_tres_cortes_estan_llenos(self, ds: Dataset) -> None:
        assert all(len(getattr(ds, s)) > 0 for s in ("train", "val", "test"))

    def test_los_cortes_no_comparten_muestras(self, ds: Dataset) -> None:
        def ids(muestras: list[Sample]) -> set[tuple[int, int]]:
            return {(m.session_idx, m.window_start_idx) for m in muestras}
        assert not ids(ds.train) & ids(ds.val)
        assert not ids(ds.val) & ids(ds.test)
        assert not ids(ds.train) & ids(ds.test)

    def test_las_velas_de_cortes_distintas_no_se_solapan(self, ds: Dataset) -> None:
        """La regla fuerte: ninguna vela observada por train aparece en val, ni
        val en test. Si esto falla, el embargo no sirve y las métricas mintieren."""
        assert not _velas_de(ds.train) & _velas_de(ds.val)
        assert not _velas_de(ds.val) & _velas_de(ds.test)
        assert not _velas_de(ds.train) & _velas_de(ds.test)

    def test_los_cortes_estan_ordenados_en_el_tiempo(self, ds: Dataset) -> None:
        assert max(s.window_end_ts for s in ds.train) < min(s.window_end_ts for s in ds.val)
        assert max(s.window_end_ts for s in ds.val) < min(s.window_end_ts for s in ds.test)

    def test_el_embargo_de_train_a_val_es_de_al_menos_quince_velas(self, ds: Dataset) -> None:
        fin_train = max(s.label_end_idx for s in ds.train)
        inicio_val = min(s.window_start_idx for s in ds.val)
        assert inicio_val - fin_train - 1 >= EMBARGO

    def test_el_embargo_de_val_a_test_es_de_al_menos_quince_velas(self, ds: Dataset) -> None:
        fin_val = max(s.label_end_idx for s in ds.val)
        inicio_test = min(s.window_start_idx for s in ds.test)
        assert inicio_test - fin_val - 1 >= EMBARGO

    def test_el_embargo_es_exactamente_la_ventana_mas_el_horizonte(self) -> None:
        assert EMBARGO == WINDOW + HORIZON == 15

    def test_rechaza_un_dataset_con_menos_de_tres_sesiones(self) -> None:
        muestras, inicio, _ = build_samples([_sesion(0), _sesion(1)], DatasetConfig())
        with pytest.raises(ValueError, match="al menos 3 sesiones"):
            split_with_embargo(muestras, inicio, DatasetConfig())

    def test_siempre_deja_alguna_muestra_de_test(self) -> None:
        # Aunque el 15% de 4 sesiones sea < 1, test no puede quedar vacío.
        ds = build_dataset(_velas(4), DatasetConfig())
        assert len(ds.test) > 0 and len(ds.val) > 0 and len(ds.train) > 0


# ---------------------------------------------------------------------------
# Estadísticos y normalización
# ---------------------------------------------------------------------------

class TestNormalizacion:
    def test_los_estadisticos_se_calculan_solo_sobre_train(self, ds: Dataset) -> None:
        apilado_train = np.stack([s.x for s in ds.train]).reshape(-1, N_FEATURES)
        apilado_test = np.stack([s.x for s in ds.test]).reshape(-1, N_FEATURES)
        assert np.allclose(ds.stats.mean, apilado_train.mean(axis=0), atol=1e-6)
        assert not np.allclose(ds.stats.mean, apilado_test.mean(axis=0), atol=1e-4)

    def test_train_normalizado_da_media_cero_y_desviacion_uno(self, ds: Dataset) -> None:
        x, _ = ds.matrix("train")
        media = x.reshape(-1, N_FEATURES).mean(axis=0)
        desvio = x.reshape(-1, N_FEATURES).std(axis=0)
        assert np.allclose(media, 0.0, atol=1e-4)
        assert np.allclose(desvio, 1.0, atol=1e-4)

    def test_val_y_test_normalizados_quedan_cerca_de_cero_pero_no_exactos(self, ds: Dataset) -> None:
        # Si quedaran exactamente en 0 y 1 sería señal de que se recalcularon
        # sobre ellos: exactamente la fuga que el plan §3 prohíbe.
        for split in ("val", "test"):
            x, _ = ds.matrix(split)
            assert np.isfinite(x).all()
            assert abs(x.reshape(-1, N_FEATURES).mean(axis=0)).max() < 1.0

    def test_val_y_test_estan_normalizados_con_los_estadisticos_de_train(self, ds: Dataset) -> None:
        x, _ = ds.matrix("val")
        bruto = np.stack([s.x for s in ds.val])
        esperado = (bruto - ds.stats.mean) / ds.stats.std
        assert np.allclose(x, esperado, atol=1e-5)

    def test_los_estadisticos_tienen_la_forma_de_las_features(self, ds: Dataset) -> None:
        assert ds.stats.mean.shape == (N_FEATURES,)
        assert ds.stats.std.shape == (N_FEATURES,)
        assert (ds.stats.std > 0).all()

    def test_train_stats_rechaza_sin_muestras(self) -> None:
        with pytest.raises(ValueError, match="no hay muestras"):
            train_only_stats([])

    def test_train_stats_protege_contra_desviacion_cero(self) -> None:
        # Una feature constante daría std 0 y división por cero al normalizar.
        constante = np.full((WINDOW, N_FEATURES), 3.14, dtype=np.float32)
        stats = train_only_stats([Sample(
            x=constante, y=LABEL_STABLE, session_idx=0, window_start_idx=0,
            label_end_idx=0, window_end_ts=0, label_end_ts=0, forward_return_pct=0.0,
        )])
        assert (stats.std >= 1e-6).all()

    def test_matrix_devuelve_las_etiquetas_en_el_orden_de_las_clases(self) -> None:
        # El orden DOWN/STABLE/UP es el que usan los logits y las columnas de
        # `predict_proba`; cambiarlo en silencio invertiría las decisiones.
        x, y = _dataset().matrix("train")
        assert x.dtype == np.float32 and y.dtype == np.int64
        assert set(np.unique(y)).issubset({LABEL_DOWN, LABEL_STABLE, LABEL_UP})

    def test_matrix_de_un_corte_vacio_devuelve_matrices_vacias(self) -> None:
        ds = _dataset()
        ds.train = []
        x, y = ds.matrix("train")
        assert x.shape == (0, WINDOW, N_FEATURES) and y.shape == (0,)


# ---------------------------------------------------------------------------
# Distribución y banda
# ---------------------------------------------------------------------------

class TestDistribucion:
    def test_distribution_incluye_siempre_las_tres_clases(self, ds: Dataset) -> None:
        for split in ("train", "val", "test"):
            dist = ds.distribution(split)
            assert set(dist) == set(LABEL_NAMES.values())
            assert sum(dist.values()) == len(getattr(ds, split))

    def test_distribution_of_da_porcentajes_que_suman_cien(self, ds: Dataset) -> None:
        assert sum(distribution_of(ds.train).values()) == pytest.approx(100.0)

    def test_returns_of_devuelve_un_vector(self, ds: Dataset) -> None:
        assert returns_of(ds.val).shape == (len(ds.val),)

    def test_distribution_at_band_es_monotono_en_la_banda(self) -> None:
        """Anchar la banda solo puede aumentar STABLE: es el argumento del plan §1.

        Es la propiedad que hizo que ±0,10 % no pudiera justificarse como
        "mejor equilibrio" — ensanchar nunca equilibra.
        """
        muestras, _, _ = build_samples(session_of(_velas(20)), DatasetConfig())
        retornos = returns_of(muestras)
        anteriores: dict[str, int] | None = None
        for banda in (0.02, 0.05, 0.10, 0.20):
            d = distribution_at_band(retornos, banda)
            assert sum(d.values()) == len(retornos)
            if anteriores is not None:
                assert d["STABLE"] >= anteriores["STABLE"]
            anteriores = d

    def test_distribution_at_band_con_banda_cero_no_deja_stable(self) -> None:
        retornos = np.array([0.01, -0.01, 0.0, 0.5, -0.5])
        d = distribution_at_band(retornos, 0.0)
        assert d["STABLE"] == 1  # solo el retorno exactamente cero
        assert d["UP"] == 2 and d["DOWN"] == 2

    def test_distribution_at_band_vacio_da_ceros(self) -> None:
        assert distribution_at_band(np.array([]), 0.1) == {
            "DOWN": 0, "STABLE": 0, "UP": 0,
        }


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

class TestConfiguracion:
    def test_los_defaults_son_los_del_plan(self) -> None:
        cfg = DatasetConfig()
        assert (cfg.window, cfg.horizon) == (WINDOW, HORIZON)
        assert cfg.stable_band_pct == 0.10          # banda fija del plan §1
        assert (cfg.train_frac, cfg.val_frac) == (0.70, 0.15)
        assert cfg.min_session_candles == WINDOW + HORIZON == EMBARGO

    def test_un_dataset_guarda_su_config(self, ds: Dataset) -> None:
        assert ds.config.stable_band_pct == 0.10
        assert ds.n_features == N_FEATURES
        assert ds.sizes() == {"train": len(ds.train), "val": len(ds.val), "test": len(ds.test)}

    def test_cambiar_la_banda_cambia_las_etiquetas(self) -> None:
        """Fija que la banda es un parámetro, no un número incrustado."""
        velas = _velas(10)
        una = build_dataset(velas, DatasetConfig(stable_band_pct=0.05))
        otra = build_dataset(velas, DatasetConfig(stable_band_pct=0.50))
        assert una.distribution("train") != otra.distribution("train")

    def test_el_dataset_descarta_sesiones_cortas(self) -> None:
        # 4 sesiones: la quinta, de 5 velas, no alcanza ni para ventana +
        # horizonte. Al descartarse quedan 3, justo el mínimo para cortar.
        velas = _velas(4, 78) + _sesion(4, 5)
        ds = build_dataset(velas, DatasetConfig())
        assert ds.descartadas_cortas == 1


# ---------------------------------------------------------------------------
# Carga desde InfluxDB (sin red)
# ---------------------------------------------------------------------------

class _Fila:
    def __init__(self, valores: dict) -> None:
        self.values = valores

    def get_time(self):
        import datetime
        return datetime.datetime.fromtimestamp(
            self.values["_time_ms"] / 1000, datetime.timezone.utc
        )


def _registro(ts: int, o: float, h: float | None, l: float, c: float, v: float) -> _Fila:
    valores = {"_time_ms": ts, "price_open": o, "price_close": c, "volume": v}
    if h is not None:
        valores["price_high"] = h
        valores["price_low"] = l
    return _Fila(valores)


class _Tabla:
    def __init__(self, registros: list) -> None:
        self.records = registros


class _QueryAPI:
    def __init__(self, registros: list) -> None:
        self._registros = registros

    def query(self, script: str, org: str | None = None):
        # Debe haber UN solo pivot que alinee los cinco campos en la misma fila:
        # una consulta por campo dejaría los campos desalineados en el tiempo.
        assert "pivot(rowKey" in script, "debe usar un único pivot"
        assert "_measurement" in script
        return [_Tabla(self._registros)]


class _Inventario:
    """Cliente de InfluxDB falso que devuelve registros fijos, sin red."""

    def __init__(self, registros: list) -> None:
        self._registros = registros

    def query_api(self) -> _QueryAPI:
        return _QueryAPI(self._registros)


class TestCarga:
    def _cargar(self, registros: list) -> list[Candle]:
        return load_candles(_Inventario(registros), bucket="b", org="o")

    def test_load_candles_arma_los_cinco_campos(self) -> None:
        velas = self._cargar([_registro(T0, 100.0, 105.0, 95.0, 103.0, 750.0)])
        assert len(velas) == 1
        assert (velas[0].open, velas[0].high, velas[0].low,
                velas[0].close, velas[0].volume) == (100.0, 105.0, 95.0, 103.0, 750.0)

    def test_load_candles_omite_las_incompletas(self) -> None:
        # CR-001 añadió price_high/price_low; una fila sin ellas es de antes de
        # la migración y no debe colarse con ceros inventados.
        velas = self._cargar([
            _registro(T0, 100.0, 105.0, 95.0, 103.0, 750.0),
            _registro(T0 + VELA, 100.0, None, 95.0, 103.0, 750.0),
        ])
        assert len(velas) == 1

    def test_load_candles_ordena_por_tiempo(self) -> None:
        velas = self._cargar([
            _registro(T0 + 10 * VELA, 1, 2, 0.5, 1.5, 10),
            _registro(T0, 1, 2, 0.5, 1.5, 10),
        ])
        assert [v.timestamp for v in velas] == [T0, T0 + 10 * VELA]

    def test_el_modulo_de_dataset_no_importa_torch(self) -> None:
        """M2 debe poder construir el dataset con solo el grupo `base`.

        Si `dataset.py` llegara a importar torch, el nodo que solo instala
        `base` no podría entrenar ni auditar el corte de datos.
        """
        raiz = Path(__file__).resolve().parents[1]
        bloqueo = (
            "import sys\n"
            "class Bloqueo:\n"
            "    def find_spec(self, fullname, path=None, target=None):\n"
            "        if fullname == 'torch' or fullname.startswith('torch.'):\n"
            "            raise ImportError('torch no debe importarse en src.ai.dataset')\n"
            "        return None\n"
            "sys.meta_path.insert(0, Bloqueo())\n"
            "import src.ai.dataset\n"
            "print('OK sin torch')\n"
        )
        entorno = os.environ.copy()
        entorno["PYTHONPATH"] = str(raiz)
        proc = subprocess.run(
            [sys.executable, "-c", bloqueo], capture_output=True, text=True,
            cwd=str(raiz), env=entorno, timeout=120,
        )
        assert proc.returncode == 0, proc.stderr
        assert "OK sin torch" in proc.stdout
