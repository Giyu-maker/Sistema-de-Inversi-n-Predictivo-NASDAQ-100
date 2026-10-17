"""Pruebas de las features (`src/ai/features.py`).

La parte importante aquí no es que las fórmulas devuelvan números, sino que
**no miren hacia delante** y **no crucen el cierre de sesión**. Un dataset con
fuga parece entrenar fenomenal y luego pierde dinero en producción, y ese fallo
no se ve en las métricas de train.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from src.ai.features import (
    EMA_FAST,
    EMA_SLOW,
    EMBARGO,
    FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION,
    HORIZON,
    LABEL_DOWN,
    LABEL_NAMES,
    LABEL_STABLE,
    LABEL_UP,
    N_FEATURES,
    ROLLING_K,
    SESSION_GAP_MS,
    WINDOW,
    Candle,
    extract_window,
    forward_return_pct,
    label_for,
    max_samples_per_session,
    persistence_return_pct,
    session_of,
    validate_window,
    window_to_vector,
)

MINUTO = 60_000
VELA = 5 * MINUTO
# 06/10/2026 09:30 ET, dentro del horario regular.
T0 = 1791331800000


def _vela(desplazamiento: int = 0, *, precio: float = 100.0,
          amplitud: float = 0.004, movimiento: float = 0.001,
          volumen: float = 1000.0) -> Candle:
    """Vela sintética determinista a partir de un desplazamiento en velas."""
    apertura = precio * (1.0 + movimiento * desplazamiento * 0.1)
    cierre = apertura * (1.0 + movimiento)
    alto = max(apertura, cierre) * (1.0 + amplitud)
    bajo = min(apertura, cierre) * (1.0 - amplitud)
    return Candle(
        timestamp=T0 + desplazamiento * VELA,
        open=apertura, high=alto, low=bajo, close=cierre, volume=volumen,
    )


def _sesion(n: int = WINDOW, **kwargs: float) -> list[Candle]:
    """Sesión sintética de `n` velas consecutivas de 5 minutos."""
    return [_vela(i, precio=100.0 * (1.0 + 0.0005 * i), **kwargs) for i in range(n)]


def _sesion_monotona(n: int, paso: float) -> list[Candle]:
    """Sesión con camino de precio estrictamente creciente (`paso` > 0) o decreciente."""
    velas: list[Candle] = []
    precio = 100.0
    for i in range(n):
        apertura = precio
        cierre = apertura * (1.0 + paso)
        velas.append(Candle(
            timestamp=T0 + i * VELA,
            open=apertura, high=max(apertura, cierre) * 1.001,
            low=min(apertura, cierre) * 0.999, close=cierre, volume=1000.0,
        ))
        precio = cierre
    return velas


def _repetir(vela: Candle, n: int = WINDOW) -> list[Candle]:
    """`n` velas con el mismo OHLCV pero timestamps consecutivos.

    Hace falta porque `validate_window` rechaza timestamps repetidos, y
    `[vela] * WINDOW` daría el mismo instante `n` veces. Permite aislar la
    semántica de una feature con velas idénticas.
    """
    return [
        Candle(timestamp=vela.timestamp + i * VELA, open=vela.open, high=vela.high,
               low=vela.low, close=vela.close, volume=vela.volume)
        for i in range(n)
    ]


def _indices() -> dict[str, int]:
    return {nombre: i for i, nombre in enumerate(FEATURE_NAMES)}


# ---------------------------------------------------------------------------
# Ventana y validación
# ---------------------------------------------------------------------------

class TestValidacionDeVentana:
    def test_acepta_una_ventana_correcta(self) -> None:
        validate_window(_sesion())  # no debe levantar

    def test_rechaza_una_ventana_corta_o_larga(self) -> None:
        with pytest.raises(ValueError, match=f"exactamente {WINDOW} velas"):
            validate_window(_sesion(WINDOW - 1))
        with pytest.raises(ValueError):
            validate_window(_sesion(WINDOW + 1))
        with pytest.raises(ValueError):
            validate_window([])

    def test_rechaza_velas_no_ordenadas(self) -> None:
        velas = _sesion()
        velas[3], velas[4] = velas[4], velas[3]
        with pytest.raises(ValueError, match="ordenadas"):
            validate_window(velas)

    def test_rechaza_timestamps_repetidos(self) -> None:
        velas = _sesion()
        repetida = velas[2]
        velas[2] = Candle(
            timestamp=velas[1].timestamp, open=repetida.open, high=repetida.high,
            low=repetida.low, close=repetida.close, volume=repetida.volume,
        )
        with pytest.raises(ValueError, match="ordenadas"):
            validate_window(velas)

    def test_rechaza_una_ventana_que_cruza_el_cierre_de_sesion(self) -> None:
        """Regla 1 del módulo: la ventana no puede cruzar dos sesiones."""
        # 11 velas de la primera sesión y la duodécima del día siguiente.
        velas = _sesion(WINDOW - 1)
        siguiente_dia = _vela(0, precio=100.0)
        velas.append(Candle(
            timestamp=velas[-1].timestamp + 18 * 60 * MINUTO,  # cierre → apertura
            open=siguiente_dia.open, high=siguiente_dia.high, low=siguiente_dia.low,
            close=siguiente_dia.close, volume=siguiente_dia.volume,
        ))
        with pytest.raises(ValueError, match="cruza el cierre"):
            validate_window(velas)

    def test_una_media_jornada_si_entra(self) -> None:
        # Un hueco de 10 min existe en la realidad (pausa intradía) y NO es un
        # cierre de sesión: el umbral está puesto para no descartarlo.
        velas = _sesion(WINDOW - 1)
        ultima = _vela(0, precio=100.0)
        velas.append(Candle(
            timestamp=velas[-1].timestamp + 10 * MINUTO,
            open=ultima.open, high=ultima.high, low=ultima.low,
            close=ultima.close, volume=ultima.volume,
        ))
        validate_window(velas)


class TestForma:
    def test_extract_window_devuelve_la_matriz_esperada(self) -> None:
        salida = extract_window(_sesion())
        assert salida.shape == (WINDOW, N_FEATURES)
        assert np.isfinite(salida).all()

    def test_extract_window_propaga_el_error_de_validacion(self) -> None:
        with pytest.raises(ValueError):
            extract_window(_sesion(WINDOW - 1))

    def test_el_vector_aplano_tiene_el_tamano_de_la_entrada_de_la_red(self) -> None:
        assert window_to_vector(extract_window(_sesion())).shape == (WINDOW * N_FEATURES,)
        assert window_to_vector(extract_window(_sesion())).dtype == np.float32

    def test_constantes_del_plan(self) -> None:
        assert (WINDOW, HORIZON, EMBARGO) == (12, 3, 15)
        assert EMBARGO == WINDOW + HORIZON
        assert len(FEATURE_NAMES) == N_FEATURES == 10
        assert len(set(FEATURE_NAMES)) == N_FEATURES  # sin repetidas
        assert FEATURE_SCHEMA_VERSION == "1"

    def test_muestras_utilizables_de_una_sesion_completa(self) -> None:
        # 78 velas de sesión regular: 78 - 12 - 3 + 1 = 64, el número esperado.
        assert max_samples_per_session(78) == 64
        assert max_samples_per_session(WINDOW + HORIZON) == 1
        assert max_samples_per_session(WINDOW + HORIZON - 1) == 0
        assert max_samples_per_session(0) == 0


# ---------------------------------------------------------------------------
# Semántica de cada feature
# ---------------------------------------------------------------------------

class TestSemanticaDeLasFeatures:
    def test_indices_de_las_features(self) -> None:
        idx = _indices()
        assert idx["log_ret"] == 0 and idx["from_open"] == 9

    def test_log_range_es_la_amplitud_logaritmica(self) -> None:
        velas = _sesion()
        salida = extract_window(velas)
        j = _indices()["log_range"]
        for i, vela in enumerate(velas):
            assert salida[i, j] == pytest.approx(math.log(vela.high / vela.low))

    def test_close_pos_queda_entre_cero_y_uno(self) -> None:
        # Un proveedor puede redondear y dejar el cierre un centésimo fuera del
        # rango; el recorte evita que eso contamine la normalización.
        j = _indices()["close_pos"]
        assert extract_window(_sesion())[1:, j].min() >= 0.0
        assert extract_window(_sesion())[1:, j].max() <= 1.0

    def test_close_pos_es_uno_si_cierra_en_el_maximo(self) -> None:
        vela = Candle(timestamp=T0, open=100.0, high=105.0, low=95.0, close=105.0, volume=10.0)
        resto = _repetir(vela)
        assert extract_window(resto)[0, _indices()["close_pos"]] == pytest.approx(1.0)

    def test_close_pos_es_cero_si_cierra_en_el_minimo(self) -> None:
        vela = Candle(timestamp=T0, open=100.0, high=105.0, low=95.0, close=95.0, volume=10.0)
        assert extract_window(_repetir(vela))[0, _indices()["close_pos"]] == pytest.approx(0.0)

    def test_body_pos_es_cero_en_una_doji(self) -> None:
        vela = Candle(timestamp=T0, open=100.0, high=102.0, low=98.0, close=100.0, volume=10.0)
        assert extract_window(_repetir(vela))[0, _indices()["body_pos"]] == pytest.approx(0.0)

    def test_body_pos_es_positivo_en_bajista_y_al_reves(self) -> None:
        # Se define (cierre - apertura) / rango, así que una vela que cierra
        # por DEBAJO de su apertura da valor NEGATIVO (presión vendedora).
        bajista = Candle(timestamp=T0, open=100.0, high=101.0, low=95.0, close=96.0, volume=10.0)
        alcista = Candle(timestamp=T0, open=100.0, high=105.0, low=99.0, close=104.0, volume=10.0)
        assert extract_window(_repetir(bajista))[0, _indices()["body_pos"]] < 0
        assert extract_window(_repetir(alcista))[0, _indices()["body_pos"]] > 0

    def test_ema_trend_es_cero_con_precios_planos(self) -> None:
        vela = Candle(timestamp=T0, open=100.0, high=101.0, low=99.0, close=100.0, volume=10.0)
        salida = extract_window(_repetir(vela))
        assert np.allclose(salida[:, _indices()["ema_trend"]], 0.0, atol=1e-9)

    def test_vol_rel_es_uno_en_una_ventana_de_volumen_constante(self) -> None:
        vela = Candle(timestamp=T0, open=100.0, high=101.0, low=99.0, close=100.0, volume=777.0)
        salida = extract_window(_repetir(vela))
        assert np.allclose(salida[:, _indices()["vol_rel"]], 1.0)

    def test_from_open_es_cero_en_su_primera_posicion(self) -> None:
        # La referencia es la apertura de la ventana, así que la primera fila
        # mide el recorrido de la propia primera vela.
        velas = _sesion()
        salida = extract_window(velas)
        j = _indices()["from_open"]
        esperado = (velas[0].close - velas[0].open) / velas[0].open
        assert salida[0, j] == pytest.approx(esperado)

    def test_log_ret_de_la_primera_fila_usa_su_apertura(self) -> None:
        velas = _sesion()
        j = _indices()["log_ret"]
        esperado = math.log(velas[0].close / velas[0].open)
        assert extract_window(velas)[0, j] == pytest.approx(esperado)

    def test_log_ret_de_las_demas_usa_el_cierre_anterior(self) -> None:
        velas = _sesion()
        j = _indices()["log_ret"]
        salida = extract_window(velas)
        for i in range(1, WINDOW):
            esperado = math.log(velas[i].close / velas[i - 1].close)
            assert salida[i, j] == pytest.approx(esperado)

    def test_log_ret_es_positivo_en_subida_y_negativo_en_bajada(self) -> None:
        # Camino de precio explícito: `_sesion` ya arrastra una deriva propia,
        # y con ella una sesión "a la baja" seguiría subiendo.
        j = _indices()["log_ret"]
        assert extract_window(_sesion_monotona(WINDOW, +0.002))[1:, j].mean() > 0
        assert extract_window(_sesion_monotona(WINDOW, -0.002))[1:, j].mean() < 0

    def test_ret_vol_y_range_vol_usan_ventana_movil(self) -> None:
        # Con rango y retorno constantes, la volatilidad móvil debe ser cero.
        vela = Candle(timestamp=T0, open=100.0, high=102.0, low=98.0, close=100.0, volume=10.0)
        salida = extract_window(_repetir(vela))
        assert np.allclose(salida[:, _indices()["ret_vol"]], 0.0, atol=1e-9)
        assert np.allclose(salida[:, _indices()["range_vol"]], 0.04, atol=1e-6)

    def test_los_hiperparametros_de_las_features_son_los_del_plan(self) -> None:
        assert (EMA_FAST, EMA_SLOW, ROLLING_K) == (3, 8, 6)


class TestSinFugaEnLasFeatures:
    """Lo más importante del archivo: nada mira hacia delante."""

    def test_modificar_la_ultima_vela_no_cambia_las_filas_previas(self) -> None:
        velas = _sesion()
        base = extract_window(velas)

        perturbadas = list(velas)
        perturbadas[-1] = Candle(
            timestamp=velas[-1].timestamp, open=velas[-1].open * 1.5,
            high=velas[-1].high * 1.5, low=velas[-1].low * 1.5,
            close=velas[-1].close * 1.5, volume=velas[-1].volume * 9,
        )
        cambiadas = extract_window(perturbadas)

        # vol_rel se normaliza contra el medio de la ventana, así que sí se
        # ve afectado; es relativo a la ventana, no una fuga temporal.
        excepcion = FEATURE_NAMES.index("vol_rel")
        columnas = [j for j in range(N_FEATURES) if j != excepcion]
        assert np.allclose(base[:-1, columnas], cambiadas[:-1, columnas], atol=1e-12)

    def test_vol_rel_si_depende_de_toda_la_ventana_y_no_debe_ocultarse(self) -> None:
        velas = _sesion()
        perturbadas = list(velas)
        perturbadas[-1] = Candle(
            timestamp=velas[-1].timestamp, open=velas[-1].open, high=velas[-1].high,
            low=velas[-1].low, close=velas[-1].close, volume=velas[-1].volume * 10,
        )
        j = _indices()["vol_rel"]
        assert not np.allclose(
            extract_window(velas)[:, j], extract_window(perturbadas)[:, j]
        )

    def test_las_features_no_dependen_de_nada_fuera_de_la_ventana(self) -> None:
        """Añadir velas antes o después no altera la matriz de la ventana."""
        dentro = _sesion(WINDOW)
        mas_antes = [_vela(-1, precio=90.0)] * 2 + dentro
        mas_despues = dentro + [_vela(WINDOW + i, precio=90.0) for i in range(5)]
        referencia = extract_window(dentro)
        assert np.allclose(referencia, extract_window(mas_antes[-WINDOW:]), atol=1e-12)
        assert np.allclose(referencia, extract_window(mas_despues[:WINDOW]), atol=1e-12)

    def test_las_mismas_velas_dan_las_mismas_features_de_vela_a_vela(self) -> None:
        """Las features de vela a vela no dependen de dónde caiga la vela en la ventana.

        Es la propiedad que hace que dos ventanas consecutivas compartan
        **11 de sus 12 velas**, y por eso el plan §4 mide la incertidumbre con
        bootstrap por bloques de sesión y no con intervalos binomiales i.i.d.:
        las muestras se solapan, no son independientes.

        Se comparan las cuatro columnas estrictamente de vela a vela. Las
        demás quedan fuera por diseño, no por error, y cada una por su motivo:
        `vol_rel` y `from_open` dependen de la primera vela de la ventana,
        `ema_trend` siembra su cadena EMA ahí, `log_ret` usa la apertura propia
        en la fila 0, y `ret_vol` promedia ese `log_ret` de la fila 0 dentro de
        su ventana móvil.
        """
        velas = [_vela(i, precio=100.0 * (1.0 + 0.0005 * i), volumen=800.0 + 37 * i)
                 for i in range(WINDOW + 1)]
        una = extract_window(velas[:WINDOW])
        otra = extract_window(velas[1:WINDOW + 1])
        puras = {"log_range", "close_pos", "body_pos", "log_vol"}
        columnas = [j for j in range(N_FEATURES) if FEATURE_NAMES[j] in puras]
        assert len(columnas) == 4
        assert np.allclose(una[1:, columnas], otra[:-1, columnas], atol=1e-12)

    def test_from_open_es_relativa_a_la_primera_vela_de_la_ventana(self) -> None:
        """Documenta y fija que `from_open` mide contra la apertura de la ventana.

        Si mañana se redefiniera contra otra referencia sin darse cuenta, este
        test lo delataría.
        """
        velas = _sesion(WINDOW)
        j = FEATURE_NAMES.index("from_open")
        base = extract_window(velas)
        # Cambiar SOLO la primera vela debe alterar from_open en TODAS las filas.
        desplazadas = list(velas)
        d = velas[0]
        desplazadas[0] = Candle(
            timestamp=d.timestamp, open=d.open * 2.0, high=d.high * 2.0,
            low=d.low * 2.0, close=d.close * 2.0, volume=d.volume,
        )
        assert not np.allclose(base[:, j], extract_window(desplazadas)[:, j])


# ---------------------------------------------------------------------------
# Sesiones
# ---------------------------------------------------------------------------

class TestDeteccionDeSesiones:
    def test_una_sesion_consecutiva_es_una_sola(self) -> None:
        assert len(session_of(_sesion(78))) == 1

    def test_un_hueco_nocturno_abre_sesion_nueva(self) -> None:
        primera = _sesion(20)
        segunda = [_vela(i + 20, precio=150.0) for i in range(20)]
        segunda = [
            Candle(timestamp=c.timestamp + 18 * 60 * MINUTO, open=c.open,
                   high=c.high, low=c.low, close=c.close, volume=c.volume)
            for c in segunda
        ]
        sesiones = session_of(primera + segunda)
        assert len(sesiones) == 2
        assert len(sesiones[0]) == 20 and len(sesiones[1]) == 20

    def test_un_hueco_intradia_no_abre_sesion_nueva(self) -> None:
        velas = _sesion(10)
        con_pausa = velas[:5] + [
            Candle(timestamp=velas[5].timestamp + 10 * MINUTO, **{
                k: getattr(velas[5], k) for k in ("open", "high", "low", "close", "volume")
            })
        ] + [
            Candle(timestamp=velas[i].timestamp + 10 * MINUTO, **{
                k: getattr(velas[i], k) for k in ("open", "high", "low", "close", "volume")
            })
            for i in range(6, 10)
        ]
        assert len(session_of(con_pausa)) == 1

    def test_el_umbral_de_sesion_es_de_60_minutos(self) -> None:
        assert SESSION_GAP_MS == 60 * MINUTO

    def test_ordena_ignorando_el_orden_de_entrada(self) -> None:
        velas = _sesion(10)
        assert session_of(list(reversed(velas)))[0][0].timestamp == velas[0].timestamp

    def test_descarta_duplicados_del_proveedor(self) -> None:
        velas = _sesion(10)
        con_duplicados = velas + [velas[3], velas[3]]
        assert [c.timestamp for c in session_of(con_duplicados)[0]] == \
            [c.timestamp for c in velas]

    def test_sin_velas_devuelve_vacio(self) -> None:
        assert session_of([]) == []


# ---------------------------------------------------------------------------
# Etiquetas y retornos
# ---------------------------------------------------------------------------

class TestRetornos:
    def test_forward_return_mide_del_cierre_de_la_ventana_al_del_horizonte(self) -> None:
        velas = _sesion(WINDOW + HORIZON)
        esperado = (velas[WINDOW - 1 + HORIZON].close - velas[WINDOW - 1].close) \
            / velas[WINDOW - 1].close * 100.0
        assert forward_return_pct(velas, 0) == pytest.approx(esperado)

    def test_forward_return_no_usa_velas_del_horizonte_de_mas(self) -> None:
        velas = _sesion(WINDOW + HORIZON + 3)
        recortada = velas[:WINDOW + HORIZON]
        assert forward_return_pct(velas, 0) == pytest.approx(forward_return_pct(recortada, 0))

    def test_persistencia_mide_las_velas_previas_no_las_futuras(self) -> None:
        velas = _sesion(WINDOW + HORIZON)
        esperado = (velas[WINDOW - 1].close - velas[WINDOW - 1 - HORIZON].close) \
            / velas[WINDOW - 1 - HORIZON].close * 100.0
        assert persistence_return_pct(velas, 0) == pytest.approx(esperado)

    def test_persistencia_no_mira_al_horizonte(self) -> None:
        """Si la persistencia mirara hacia delante sería un baseline tramposo."""
        velas = _sesion(WINDOW + HORIZON)
        perturbadas = list(velas)
        h = velas[WINDOW]  # primera vela del horizonte: debe ser indiferente
        perturbadas[WINDOW] = Candle(
            timestamp=h.timestamp, open=h.open * 3, high=h.high * 3,
            low=h.low * 3, close=h.close * 3, volume=h.volume,
        )
        assert persistence_return_pct(velas, 0) == pytest.approx(
            persistence_return_pct(perturbadas, 0)
        )

    def test_persistencia_y_forward_usan_referencias_distintas(self) -> None:
        velas = _sesion(WINDOW + HORIZON)
        assert forward_return_pct(velas, 0) != pytest.approx(persistence_return_pct(velas, 0))


class TestEtiquetas:
    def test_los_limites_de_la_banda_son_estrictos(self) -> None:
        banda = 0.10
        assert label_for(0.1001, banda) == LABEL_UP
        assert label_for(0.10, banda) == LABEL_STABLE   # el límite queda dentro
        assert label_for(-0.10, banda) == LABEL_STABLE
        assert label_for(-0.1001, banda) == LABEL_DOWN
        assert label_for(0.0, banda) == LABEL_STABLE

    def test_las_etiquetas_coinciden_con_sus_nombres(self) -> None:
        assert LABEL_NAMES == {LABEL_DOWN: "DOWN", LABEL_STABLE: "STABLE", LABEL_UP: "UP"}
        assert (LABEL_DOWN, LABEL_STABLE, LABEL_UP) == (0, 1, 2)

    def test_la_banda_por_defecto_es_la_fija_del_plan(self) -> None:
        from src.ai.dataset import DatasetConfig
        assert DatasetConfig().stable_band_pct == 0.10
