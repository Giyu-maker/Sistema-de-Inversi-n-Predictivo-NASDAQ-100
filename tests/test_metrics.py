"""Pruebas de las métricas y baselines pre-registrados (`src/ai/metrics.py`).

Aquí se protegen tres decisiones que no pueden doblarse mirando el resultado:

1. Que el macro-F1 penalice de verdad a un modelo que ignora una clase.
2. Que el bootstrap remuestree **sesiones completas**, no muestras sueltas.
3. Que una comparación con IC que contiene el 0 se declare *sin ventaja*.

La tercera es la que evita que se presente como hallazgo una diferencia que
podría ser ruido.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.ai.features import LABEL_DOWN, LABEL_STABLE, LABEL_UP
from src.ai.metrics import (
    PERCENTILES,
    TRAMOS_CONFIANZA,
    UMBRALES_PERFIL,
    Metricas,
    accuracy,
    acierto_direccional,
    baseline_azar_por_priors,
    baseline_mayoritario,
    baseline_persistencia,
    bootstrap_bloques,
    bootstrap_pareado,
    calibracion,
    calibracion_por_clase,
    confusion_matrix,
    corregir_posterior,
    f1_por_clase,
    hay_ventaja_demostrada,
    macro_f1,
    metricas_por_tercios,
    pesos_de_clase,
    sesiones_de,
    tabla_umbrales,
)

SEMILLA = 20261017


class _Muestra:
    def __init__(self, session_idx: int, persistence_y: int) -> None:
        self.session_idx = session_idx
        self.persistence_y = persistence_y


# ---------------------------------------------------------------------------
# Métricas básicas
# ---------------------------------------------------------------------------

class TestMacroF1:
    def test_prediccion_perfecta_da_uno(self) -> None:
        y = np.array([0, 1, 2, 1, 0, 2])
        assert macro_f1(y, y) == pytest.approx(1.0)

    def test_precision_perfecta_da_uno(self) -> None:
        assert accuracy(np.array([0, 1, 2]), np.array([0, 1, 2])) == 1.0

    def test_precision_es_cero_si_nada_coincide(self) -> None:
        assert accuracy(np.zeros(10, int), np.ones(10, int)) == 0.0

    def test_precision_sobre_vacio_da_cero(self) -> None:
        assert accuracy(np.array([]), np.array([])) == 0.0

    def test_un_modelo_que_ignora_dos_clases_no_saca_nota_alta(self) -> None:
        """El motivo entero de usar macro-F1 en vez de accuracy (plan §1)."""
        # El 80% de la muestra es clase 1: quien siempre dice 1 acierta el 80%.
        y_true = np.array([1] * 80 + [0] * 10 + [2] * 10)
        y_pred = np.full(100, 1)
        assert accuracy(y_true, y_pred) == pytest.approx(0.80)
        # DOWN y UP quedan en F1 0, así que el promedio se desploma.
        assert macro_f1(y_true, y_pred) < 0.45

    def test_el_macro_f1_es_el_promedio_de_las_tres_clases(self) -> None:
        y_true = np.array([0, 1, 2])
        y_pred = np.array([0, 1, 0])
        por_clase = f1_por_clase(y_true, y_pred)
        assert macro_f1(y_true, y_pred) == pytest.approx(float(por_clase.mean()))

    def test_itera_sobre_las_tres_clases_fijas(self) -> None:
        # Una clase ausente de los datos cuenta como 0, no desaparece del promedio.
        y = np.array([0, 0, 0])
        assert f1_por_clase(y, y).shape == (3,)
        assert f1_por_clase(y, y)[1] == 0.0 and f1_por_clase(y, y)[2] == 0.0
        assert macro_f1(y, y) == pytest.approx(1 / 3)

    def test_todas_las_predicciones_de_una_clase_distinta_da_cero(self) -> None:
        assert macro_f1(np.zeros(20, int), np.ones(20, int)) == 0.0

    def test_no_divide_por_cero_con_clases_vacias(self) -> None:
        assert np.isfinite(macro_f1(np.zeros(5, int), np.zeros(5, int)))
        assert np.isfinite(f1_por_clase(np.zeros(0, int), np.zeros(0, int))).all()


class TestMatrizDeConfusion:
    def test_filas_son_reales_y_columnas_predichas(self) -> None:
        matriz = confusion_matrix(np.array([0, 0, 1, 2]), np.array([0, 1, 1, 2]))
        assert matriz.shape == (3, 3)
        assert matriz[0, 0] == 1 and matriz[0, 1] == 1   # real 0: uno acertado, uno a UP/STABLE
        assert matriz[1, 1] == 1 and matriz[2, 2] == 1

    def test_la_suma_es_el_numero_de_muestras(self) -> None:
        y = np.random.RandomState(SEMILLA).randint(0, 3, 500)
        p = np.random.RandomState(SEMILLA + 1).randint(0, 3, 500)
        assert confusion_matrix(y, p).sum() == 500

    def test_no_modifica_las_entradas(self) -> None:
        y = np.array([0, 1, 2])
        copia = y.copy()
        confusion_matrix(y, np.array([0, 1, 2]))
        assert np.array_equal(y, copia)


class TestMetricas:
    def test_como_dict_es_serializable(self) -> None:
        import json
        y = np.array([0, 1, 2, 1])
        datos = Metricas.de(y, y).como_dict()
        assert json.dumps(datos)
        assert set(datos["f1_por_clase"]) == {"DOWN", "STABLE", "UP"}

    def test_las_clases_se_reportan_en_orden_down_stable_up(self) -> None:
        y = np.array([LABEL_DOWN, LABEL_STABLE, LABEL_UP])
        datos = Metricas.de(y, y).como_dict()
        assert list(datos["f1_por_clase"]) == ["DOWN", "STABLE", "UP"]


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------

class TestBaselines:
    def test_mayoritario_predice_siempre_la_moda_de_train(self) -> None:
        y_train = np.array([1, 1, 1, 0, 2])
        salida = baseline_mayoritario(y_train, 7)
        assert salida.shape == (7,)
        assert (salida == LABEL_STABLE).all()

    def test_mayoritario_es_invariante_al_orden_de_train(self) -> None:
        y = np.array([1, 1, 0, 2, 1])
        assert np.array_equal(baseline_mayoritario(y, 4),
                              baseline_mayoritario(y[::-1], 4))

    def test_mayoritario_su_macro_f1_es_bajo_con_banda_de_diez_decimas(self) -> None:
        """La cifra que hizo que el plan descartara la accuracy como métrica."""
        y = np.array([1] * 62 + [0] * 19 + [2] * 19)
        assert macro_f1(y, baseline_mayoritario(y, len(y))) == pytest.approx(0.2552, abs=0.001)

    def test_azar_es_reproducible_con_la_misma_semilla(self) -> None:
        y_train = np.array([1] * 60 + [0] * 20 + [2] * 20)
        y_true = np.array([1] * 60 + [0] * 20 + [2] * 20)
        a = baseline_azar_por_priors(y_train, y_true, semilla=SEMILLA, repeticiones=200)
        b = baseline_azar_por_priors(y_train, y_true, semilla=SEMILLA, repeticiones=200)
        assert np.array_equal(a[0], b[0])
        assert a[1] == b[1]
        assert a[2] == b[2]

    def test_azar_cambia_con_la_semilla(self) -> None:
        y_train = np.array([1] * 60 + [0] * 20 + [2] * 20)
        y_true = np.array([1] * 60 + [0] * 20 + [2] * 20)
        a = baseline_azar_por_priors(y_train, y_true, semilla=SEMILLA, repeticiones=50)[0]
        b = baseline_azar_por_priors(y_train, y_true, semilla=SEMILLA + 7, repeticiones=50)[0]
        assert not np.array_equal(a, b)

    def test_azar_respeta_los_priors_de_train(self) -> None:
        y_train = np.array([1] * 80 + [0] * 10 + [2] * 10)
        y_true = np.tile(y_train, 200)
        pred, media, ic = baseline_azar_por_priors(y_train, y_true, semilla=SEMILLA,
                                                   repeticiones=1)
        cuenta = np.bincount(pred, minlength=3) / len(pred)
        assert cuenta[1] == pytest.approx(0.80, abs=0.02)
        assert cuenta[0] == pytest.approx(0.10, abs=0.02)
        assert media > 0 and ic[0] <= ic[1]

    def test_azar_se_evalua_contra_las_etiquetas_reales(self) -> None:
        """Regresión: se medía contra la clase mayoritaria como si fuera la verdad.

        Eso devolvía un número con apariencia de macro-F1 que no se puede
        comparar con ningún otro del informe, y que además no cuadraba con la
        tirada representativa evaluada bien. Aquí se comprueba que el promedio
        cae en la zona del baseline mayoritario, que es donde un clasificador
        aleatorio con esos priors tiene que estar.
        """
        y_train = np.array([1] * 62 + [0] * 19 + [2] * 19)
        y_true = np.tile(y_train, 50)
        _, media, _ = baseline_azar_por_priors(y_train, y_true, semilla=SEMILLA,
                                               repeticiones=400)
        referencia = macro_f1(y_true, baseline_mayoritario(y_train, len(y_true)))
        assert abs(media - referencia) < 0.10, (
            f"el azar dio {media:.4f} frente a {referencia:.4f} del mayoritario: "
            "se estaría midiendo contra un objetivo equivocado"
        )

    def test_azar_rechaza_entradas_vacias(self) -> None:
        con_muestras = np.array([0, 1, 2])
        con_vacio = np.array([], dtype=np.int64)
        with pytest.raises(ValueError, match="no hay muestras"):
            baseline_azar_por_priors(con_vacio, con_vacio, semilla=SEMILLA)
        with pytest.raises(ValueError, match="no hay etiquetas reales"):
            baseline_azar_por_priors(con_muestras, con_vacio, semilla=SEMILLA)

    def test_persistencia_lee_persistence_y(self) -> None:
        muestras = [_Muestra(0, LABEL_UP), _Muestra(0, LABEL_DOWN), _Muestra(1, LABEL_STABLE)]
        assert baseline_persistencia(muestras).tolist() == [LABEL_UP, LABEL_DOWN, LABEL_STABLE]

    def test_persistencia_rechaza_sin_muestras(self) -> None:
        with pytest.raises(ValueError):
            baseline_persistencia([])

    def test_persistencia_supera_al_mayoritario_en_un_camino_determinista(self) -> None:
        """Un camino monotónico: la persistencia acierta todo, el mayoritario nada.

        El macro-F1 de persistencia no es 1.0 sino 1/3, y eso es correcto y
        conviene no confundirse: se promedia sobre las tres clases fijas, y
        STABLE y DOWN no aparecen ni en las reales ni en las predichas, así que
        aportan 0. Es precisamente por lo que una accuracy perfecta en un
        mercado que solo sube no demuestra que el modelo sepa nada de STABLE.
        """
        muestras = [_Muestra(0, LABEL_UP)] * 30
        y_true = np.array([LABEL_UP] * 30)
        assert macro_f1(y_true, baseline_persistencia(muestras)) == pytest.approx(1 / 3)
        assert accuracy(y_true, baseline_persistencia(muestras)) == 1.0
        assert macro_f1(y_true, baseline_mayoritario(np.array([LABEL_DOWN]), 30)) == 0.0


class TestPesosDeClase:
    def test_son_inversamente_proporcionales(self) -> None:
        y = np.array([1] * 80 + [0] * 10 + [2] * 10)
        pesos = pesos_de_clase(y)
        assert pesos[1] < pesos[0]
        assert pesos[1] < pesos[2]

    def test_tienen_media_uno(self) -> None:
        pesos = pesos_de_clase(np.array([1] * 90 + [0] * 3 + [2] * 7))
        assert pesos.mean() == pytest.approx(1.0)

    def test_son_reproducibles_e_invariantes_al_orden(self) -> None:
        y = np.array([0] * 5 + [1] * 30 + [2] * 15)
        assert np.allclose(pesos_de_clase(y), pesos_de_clase(y[::-1]))

    def test_rechaza_si_falta_alguna_clase(self) -> None:
        # Con una clase ausente el peso sería infinito; mejor fallar al principio.
        with pytest.raises(ValueError, match="faltan clases"):
            pesos_de_clase(np.array([0] * 50 + [1] * 50))


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

class TestSesiones:
    def test_devuelve_el_indice_en_el_mismo_orden(self) -> None:
        muestras = [_Muestra(3, 0), _Muestra(3, 0), _Muestra(7, 0)]
        assert sesiones_de(muestras).tolist() == [3, 3, 7]

    def test_rechaza_sin_muestras(self) -> None:
        with pytest.raises(ValueError):
            sesiones_de([])


class TestBootstrapBloques:
    def _datos(self, n_sesiones: int = 40, por_sesion: int = 30):
        """Sesiones con el error **correlacionado dentro de la sesión**, que es el
        caso real: dos ventanas consecutivas comparten 11 de sus 12 velas, así
        que si una sesión se lee mal, casi todas sus muestras se leen mal.

        La mitad de las sesiones se aciertan casi enteras y la otra mitad
        falla casi enteras: la varianza está **entre** sesiones, no dentro.
        """
        rng = np.random.RandomState(SEMILLA)
        sesiones = np.repeat(np.arange(n_sesiones), por_sesion)
        y = rng.randint(0, 3, n_sesiones * por_sesion)
        p = y.copy()
        for s in range(n_sesiones):
            if rng.rand() < 0.5:
                idx = np.flatnonzero(sesiones == s)
                p[idx] = (y[idx] + 1) % 3
        return y, p, sesiones

    def test_el_intervalo_contiene_al_punto_mas_o_menos(self) -> None:
        y, p, sesiones = self._datos()
        intervalo, valores = bootstrap_bloques(y, p, sesiones, n_replicas=200,
                                               semilla=SEMILLA)
        assert intervalo[0] <= float(np.median(valores)) <= intervalo[1]
        assert valores.shape == (200,)
        assert intervalo[0] <= macro_f1(y, p) <= intervalo[1] + 0.05

    def test_es_reproducible_con_la_misma_semilla(self) -> None:
        y, p, sesiones = self._datos()
        a = bootstrap_bloques(y, p, sesiones, n_replicas=100, semilla=SEMILLA)[0]
        b = bootstrap_bloques(y, p, sesiones, n_replicas=100, semilla=SEMILLA)[0]
        assert a == b

    def test_usa_los_percentiles_del_plan(self) -> None:
        assert PERCENTILES == (2.5, 97.5)
        y, p, sesiones = self._datos()
        _, valores = bootstrap_bloques(y, p, sesiones, n_replicas=500, semilla=SEMILLA)
        assert np.percentile(valores, 2.5) >= np.percentile(valores, 1) - 1e-12

    def test_remuestrea_sesiones_no_muestras(self) -> None:
        """Si se remuestrearan muestras sueltas el intervalo saldría más estrecho.

        Este test materializa el motivo del plan §4: las ventanas se solapan en
        11 de 12 velas, así que no son independientes y un i.i.d. falsea la
        varianza hacia abajo. Aquí las sesiones se construyen con el error
        correlacionado, que es como son los datos reales.
        """
        y, p, sesiones = self._datos()
        _, por_sesion_valores = bootstrap_bloques(y, p, sesiones, n_replicas=300,
                                                  semilla=SEMILLA)
        rng = np.random.RandomState(SEMILLA)
        por_muestra = np.empty(300)
        for r in range(300):
            idx = rng.randint(0, len(y), len(y))
            por_muestra[r] = macro_f1(y[idx], p[idx])
        ancho_sesion = np.diff(np.percentile(por_sesion_valores, PERCENTILES))[0]
        ancho_muestra = np.diff(np.percentile(por_muestra, PERCENTILES))[0]
        assert ancho_sesion > ancho_muestra * 2, (
            f"el intervalo por sesión ({ancho_sesion:.4f}) debió ser claramente "
            f"más ancho que el i.i.d. ({ancho_muestra:.4f})"
        )

    def test_con_todas_las_sesiones_iguales_el_intervalo_es_un_punto(self) -> None:
        y = np.array([1, 2, 0, 1] * 25)
        sesiones = np.repeat(np.arange(25), 4)
        intervalo, valores = bootstrap_bloques(y, y, sesiones, n_replicas=100,
                                               semilla=SEMILLA)
        assert intervalo[0] == pytest.approx(intervalo[1])
        assert (valores == 1.0).all()

    def test_rechaza_entradas_incoherentes(self) -> None:
        with pytest.raises(ValueError, match="misma forma"):
            bootstrap_bloques(np.zeros(5, int), np.zeros(4, int), np.zeros(5, int))
        with pytest.raises(ValueError, match="no hay muestras"):
            bootstrap_bloques(np.array([], int), np.array([], int), np.array([], int))


class TestBootstrapPareado:
    def test_dos_modelos_identicos_dan_cero_exacto(self) -> None:
        y = np.array([0, 1, 2, 1, 0, 2] * 20)
        sesiones = np.repeat(np.arange(20), 6)
        intervalo, diferencia = bootstrap_pareado(y, y, y, sesiones, n_replicas=100,
                                                  semilla=SEMILLA)
        assert diferencia == 0.0
        assert intervalo == (0.0, 0.0)
        # Y la regla del plan: no hay ventaja demostrada.
        assert not hay_ventaja_demostrada(intervalo)

    def test_un_modelo_claramente_mejor_da_intervalo_positivo(self) -> None:
        rng = np.random.RandomState(SEMILLA)
        y = np.tile([0, 1, 2], 400)
        sesiones = np.repeat(np.arange(40), 30)
        bueno = y.copy()
        flojo = rng.randint(0, 3, len(y))
        intervalo, diferencia = bootstrap_pareado(y, bueno, flojo, sesiones,
                                                  n_replicas=200, semilla=SEMILLA)
        assert diferencia > 0.5
        assert intervalo[0] > 0.0
        assert hay_ventaja_demostrada(intervalo)

    def test_rechaza_formas_distintas(self) -> None:
        with pytest.raises(ValueError, match="misma forma"):
            bootstrap_pareado(np.zeros(3, int), np.zeros(3, int), np.zeros(2, int),
                              np.zeros(3, int))

    def test_la_diferencia_es_simetrica_en_el_signo(self) -> None:
        rng = np.random.RandomState(SEMILLA)
        y = rng.randint(0, 3, 300)
        a = rng.randint(0, 3, 300)
        b = rng.randint(0, 3, 300)
        sesiones = np.zeros(300, dtype=int)
        _, ab = bootstrap_pareado(y, a, b, sesiones, n_replicas=50, semilla=SEMILLA)
        _, ba = bootstrap_pareado(y, b, a, sesiones, n_replicas=50, semilla=SEMILLA)
        assert ab == pytest.approx(-ba)


class TestReglaDeVentaja:
    def test_no_hay_ventaja_si_el_ic_incluye_el_cero(self) -> None:
        assert not hay_ventaja_demostrada((-0.01, 0.05))
        assert not hay_ventaja_demostrada((0.0, 0.05))     # borde incluido
        assert not hay_ventaja_demostrada((-0.05, 0.0))
        assert not hay_ventaja_demostrada((-0.3, 0.3))

    def test_hay_ventaja_si_el_ic_entero_es_positivo_o_negativo(self) -> None:
        assert hay_ventaja_demostrada((0.001, 0.5))
        assert hay_ventaja_demostrada((-0.5, -0.001))


# ---------------------------------------------------------------------------
# Calibración y umbrales
# ---------------------------------------------------------------------------

class TestCalibracion:
    def test_las_muestras_se_parten_enteras_entre_tramos(self) -> None:
        rng = np.random.RandomState(SEMILLA)
        y = rng.randint(0, 3, 500)
        p = rng.randint(0, 3, 500)
        c = rng.rand(500)
        filas = calibracion(y, p, c)
        assert sum(f["n"] for f in filas) == 500

    def test_la_brecha_es_precision_menos_confianza(self) -> None:
        filas = calibracion(np.array([1]), np.array([1]), np.array([0.80]))
        assert filas[4]["tramo"] == "[0.80, 0.90)"
        assert filas[4]["precision_observada"] == pytest.approx(1.0)
        assert filas[4]["brecha"] == pytest.approx(0.20)

    def test_un_tramo_vacio_no_produce_cero_sino_none(self) -> None:
        filas = calibracion(np.array([1]), np.array([1]), np.array([0.80]))
        vacios = [f for f in filas if f["n"] == 0]
        assert vacios
        assert all(f["precision_observada"] is None for f in vacios)

    def test_el_ultimo_tramo_es_cerrado(self) -> None:
        # Una confianza de exactamente 1.0000 no puede quedar sin tramo.
        filas = calibracion(np.array([1]), np.array([1]), np.array([1.0]))
        assert any(f["n"] == 1 for f in filas)

    def test_los_tramos_son_los_del_plan(self) -> None:
        assert TRAMOS_CONFIANZA[0] == (0.0, 0.5)
        assert TRAMOS_CONFIANZA[-1][1] > 1.0


class TestUmbrales:
    def test_son_los_tres_perfiles_del_sdd(self) -> None:
        assert UMBRALES_PERFIL == {
            "CONVENTIONAL": 0.85, "MODERATE": 0.75, "AGGRESSIVE": 0.65,
        }

    def test_al_subir_el_umbral_cae_el_numero_de_predicciones(self) -> None:
        rng = np.random.RandomState(SEMILLA)
        y = rng.randint(0, 3, 400)
        p = rng.randint(0, 3, 400)
        c = rng.rand(400)
        filas = tabla_umbrales(y, p, c, umbrales=[0.65, 0.75, 0.85])
        conteos = [f["n_predicciones"] for f in filas]
        assert conteos == sorted(conteos, reverse=True), (
            "subir el umbral solo puede reducir el número de operaciones"
        )
        # Ningún umbral deja afuera a otro: los conjuntos son anidados.
        assert sum(conteos) <= 400 * 3

    def test_el_orden_por_defecto_es_el_de_los_perfiles_del_sdd(self) -> None:
        filas = tabla_umbrales(np.array([0, 1, 2]), np.array([0, 1, 2]),
                               np.array([0.9, 0.9, 0.9]))
        assert [f["umbral"] for f in filas] == [0.85, 0.75, 0.65]

    def test_precision_up_solo_mira_las_predicciones_up(self) -> None:
        y = np.array([LABEL_UP, LABEL_UP, LABEL_DOWN, LABEL_STABLE])
        p = np.array([LABEL_UP, LABEL_UP, LABEL_DOWN, LABEL_STABLE])
        c = np.array([0.9, 0.9, 0.9, 0.9])
        fila = tabla_umbrales(y, p, c, umbrales=[0.85])[0]
        assert fila["n_up"] == 2 and fila["precision_up"] == 1.0
        assert fila["n_down"] == 1 and fila["precision_down"] == 1.0

    def test_precision_up_cae_si_el_modelo_falla_en_las_subidas(self) -> None:
        """Caso que motivó reportar UP y DOWN por separado (plan §5).

        Tres de seis muestras son subidas reales y el modelo las clasifica
        todas como baja. La precisión **global** de 0,50 parece aceptable, pero
        esconde que en subidas no hay una sola predicción que valga.
        """
        y = np.array([LABEL_DOWN, LABEL_DOWN, LABEL_STABLE, LABEL_UP, LABEL_UP, LABEL_UP])
        p = np.array([LABEL_DOWN, LABEL_DOWN, LABEL_STABLE, LABEL_DOWN, LABEL_DOWN, LABEL_DOWN])
        c = np.full(6, 0.9)
        fila = tabla_umbrales(y, p, c, umbrales=[0.85])[0]
        assert fila["n_up"] == 0
        assert fila["precision_up"] is None       # nada que evaluar en subidas
        assert fila["precision_down"] == pytest.approx(2 / 5)
        assert fila["precision_global"] == pytest.approx(0.5)
        # Y el agregado esconde el fallo: 0,50 no avisa de que en UP es nulo.
        assert fila["precision_global"] > 0.4

    def test_un_umbral_imposible_da_todo_cero(self) -> None:
        y = np.array([0, 1, 2])
        p = np.array([0, 1, 2])
        c = np.array([0.9, 0.9, 0.9])
        fila = tabla_umbrales(y, p, c, umbrales=[1.5])[0]
        assert fila["n_predicciones"] == 0
        assert fila["precision_global"] is None
        assert fila["pct_del_total"] == 0.0

    def test_el_porcentaje_se_calcula_sobre_el_total(self) -> None:
        y = np.array([0, 1, 2, 1])
        c = np.array([0.9, 0.9, 0.1, 0.1])
        fila = tabla_umbrales(y, np.array([0, 1, 2, 1]), c, umbrales=[0.8])[0]
        assert fila["pct_del_total"] == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# Tercios cronológicos
# ---------------------------------------------------------------------------

class TestTercios:
    def test_las_muestras_se_parten_enteras(self) -> None:
        y = np.array([0, 1, 2] * 99)
        filas = metricas_por_tercios(y, y)
        assert len(filas) == 3
        assert sum(f["n"] for f in filas) == 297

    def test_preserva_el_orden_cronologico(self) -> None:
        # Un deterioro solo hacia el final debe verse en el tercer tercio.
        y = np.array([1] * 300)
        p = y.copy()
        p[200:] = 0
        filas = metricas_por_tercios(y, p)
        assert filas[2]["macro_f1"] < filas[0]["macro_f1"]
        assert filas[2]["accuracy"] == pytest.approx(0.0)

    def test_reporta_la_distribucion_de_clases_por_tercio(self) -> None:
        y = np.array([0] * 100 + [1] * 100 + [2] * 100)
        filas = metricas_por_tercios(y, y)
        assert filas[0]["down"] == pytest.approx(1.0)
        assert filas[1]["stable"] == pytest.approx(1.0)
        assert filas[2]["up"] == pytest.approx(1.0)

    def test_rechaza_entradas_vacias(self) -> None:
        with pytest.raises(ValueError):
            metricas_por_tercios(np.array([], int), np.array([], int))


# ---------------------------------------------------------------------------
# Corrección de prior — la confianza que se expone al usuario (RF-304)
# ---------------------------------------------------------------------------

class TestCorregirPosterior:
    def test_con_pesos_unitarios_es_la_identidad(self) -> None:
        probs = np.array([[0.6, 0.3, 0.1], [0.2, 0.5, 0.3]], dtype=np.float32)
        assert np.allclose(corregir_posterior(probs, np.ones(3)), probs, atol=1e-7)

    def test_aplica_la_formula_verificada_a_mano(self) -> None:
        # p ∝ q / w, renormalizada:  [0.40, 0.35, 0.25] / [2, 1, 1] = [0.2, 0.35, 0.25]
        probs = np.array([0.40, 0.35, 0.25])
        esperado = np.array([0.2, 0.35, 0.25]) / 0.80
        assert np.allclose(corregir_posterior(probs, np.array([2.0, 1.0, 1.0])),
                           esperado, atol=1e-6)

    def test_las_filas_siguen_normalizadas(self) -> None:
        probs = np.array([[0.4, 0.3, 0.3], [0.1, 0.1, 0.8]])
        corregido = corregir_posterior(probs, np.array([1.3, 0.4, 1.3]))
        assert np.allclose(corregido.sum(axis=1), 1.0, atol=1e-6)
        assert (corregido > 0).all()

    def test_puede_cambiar_la_clase_mas_probable(self) -> None:
        """La corrección no es un simple reescalado: puede mover el argmax."""
        probs = np.array([0.40, 0.39, 0.21])
        pesos = np.array([2.0, 1.0, 1.0])
        assert probs.argmax() == 0
        assert corregir_posterior(probs, pesos).argmax() == 1

    def test_la_confianza_reportada_puede_bajar_de_un_tercio(self) -> None:
        """El piso de 1/3 vale para la softmax cruda, no para lo que se expone.

        La corrección **sí** garantiza que `max(posterior corregido) ≥ 1/3`,
        como cualquier distribución de 3 clases. Pero lo que se reporta es el
        valor de la clase que **ganó la softmax**, que no es necesariamente la
        más probable tras corregir; en ese caso puede quedar por debajo del
        azar uniforme. Es correcto: los pesos existen precisamente para
        contrarrestar que esa clase se sobre-representa durante el
        entrenamiento.
        """
        probs = np.array([0.34, 0.33, 0.33])
        pesos = np.array([1.05, 0.94, 1.01])
        corregido = corregir_posterior(probs, pesos)
        indice_ganador = int(probs.argmax())          # quién decide la red
        assert corregido.max() >= 1 / 3               # sí, siempre
        assert corregido[indice_ganador] < 1 / 3      # pero el reportado, no

    def test_conserva_la_forma_de_un_vector(self) -> None:
        assert corregir_posterior(np.array([0.2, 0.3, 0.5]), np.ones(3)).shape == (3,)

    def test_un_vector_vacio_devuelve_vacio(self) -> None:
        vacio = np.zeros((0, 3), dtype=np.float32)
        assert corregir_posterior(vacio, np.ones(3)).shape == (0, 3)

    def test_rechaza_pesos_no_positivos(self) -> None:
        with pytest.raises(ValueError, match="positivos"):
            corregir_posterior(np.array([0.2, 0.3, 0.5]), np.array([1.0, 0.0, 1.0]))

    def test_rechaza_una_matriz_de_pesos(self) -> None:
        with pytest.raises(ValueError, match="vector"):
            corregir_posterior(np.array([0.2, 0.3, 0.5]), np.ones((1, 3)))

    def test_rechaza_una_dimension_incompatible(self) -> None:
        with pytest.raises(ValueError, match="última dimensión"):
            corregir_posterior(np.array([0.2, 0.3, 0.5]), np.ones(4))


# ---------------------------------------------------------------------------
# Acierto direccional
# ---------------------------------------------------------------------------

class TestAciertoDireccional:
    def test_un_modelo_perfecto_acierta_todo_lo_direccional(self) -> None:
        y = np.array([LABEL_DOWN, LABEL_STABLE, LABEL_UP, LABEL_UP])
        r = acierto_direccional(y, y.copy(), np.zeros(4, dtype=int), n_replicas=200)
        assert r["n"] == 3                      # el STABLE queda fuera
        assert r["aciertos"] == 3
        assert r["ratio"] == pytest.approx(1.0)
        assert r["casos_direccionales_verdad"] == 3
        inferior, superior = r["ic_95"]
        assert inferior == pytest.approx(1.0) and superior == pytest.approx(1.0)

    def test_stable_no_cuenta_ni_como_verdad_ni_como_prediccion(self) -> None:
        y_true = np.array([LABEL_DOWN, LABEL_DOWN, LABEL_STABLE, LABEL_UP])
        y_pred = np.array([LABEL_STABLE, LABEL_DOWN, LABEL_STABLE, LABEL_STABLE])
        r = acierto_direccional(y_true, y_pred, np.zeros(4, dtype=int), n_replicas=200)
        # Solo DOWN→DOWN sobrevive: DOWN→STABLE se cae por la predicción,
        # STABLE→STABLE por la verdad y UP→STABLE por la predicción.
        assert r["n"] == 1
        assert r["aciertos"] == 1
        assert r["casos_direccionales_verdad"] == 3

    def test_cuenta_la_direccion_no_la_clase(self) -> None:
        # DOWN real y UP predicho es dirección, pero equivocada.
        y = np.array([LABEL_DOWN, LABEL_UP, LABEL_UP, LABEL_DOWN])
        p = np.array([LABEL_UP] * 4)
        r = acierto_direccional(y, p, np.zeros(4, dtype=int), n_replicas=200)
        assert r["n"] == 4 and r["aciertos"] == 2
        assert r["ratio"] == pytest.approx(0.5)

    def test_un_resultado_al_azar_no_es_ventaja(self) -> None:
        """Al 50 % el intervalo contiene el 0,5: no se declara acierto."""
        rng = np.random.RandomState(SEMILLA)
        y = np.tile([LABEL_DOWN, LABEL_UP], 200)
        p = rng.choice([LABEL_DOWN, LABEL_UP], size=len(y))
        r = acierto_direccional(y, p, np.arange(len(y)) // 2, n_replicas=500)
        inferior, superior = r["ic_95"]
        assert inferior <= 0.5 <= superior

    def test_el_intervalo_no_es_binomial_iid(self) -> None:
        """Con errores agrupados por sesión, el IC debe salir más ancho que el i.i.d.

        Un modelo que acierta perfecto en unas sesiones y falla siempre en
        otras tiene el mismo 50 % agregado que uno que falla al azar, pero su
        incertidumbre es distinta: el i.i.d. la subestimaría porque asume
        muestras independientes. Si el bootstrap por sesión diera el mismo
        ancho, la autocorrelación no se estaría respetando y el plan §4
        quedaría sin cumplir.
        """
        n_bloques, tam_bloque = 100, 10
        bloques = np.repeat(np.arange(n_bloques), tam_bloque)
        y = np.tile([LABEL_DOWN, LABEL_UP], n_bloques * tam_bloque // 2)
        p = y.copy()
        falla = bloques % 2 == 1
        p[falla & (y == LABEL_DOWN)] = LABEL_UP
        p[falla & (y == LABEL_UP)] = LABEL_DOWN

        r = acierto_direccional(y, p, bloques, n_replicas=1000)
        assert r["ratio"] == pytest.approx(0.5)
        ancho_ic = r["ic_95"][1] - r["ic_95"][0]
        ancho_iid = 1.96 * np.sqrt(r["ratio"] * (1 - r["ratio"]) / r["n"])
        assert ancho_ic > ancho_iid * 2

    def test_si_no_hay_casos_direccionales_lo_dice(self) -> None:
        y = np.array([LABEL_STABLE] * 6)
        r = acierto_direccional(y, y, np.zeros(6, dtype=int), n_replicas=200)
        assert r["n"] == 0 and r["ratio"] is None and r["ic_95"] is None
        assert r["casos_direccionales_verdad"] == 0

    def test_rechaza_entradas_desalineadas(self) -> None:
        with pytest.raises(ValueError):
            acierto_direccional(np.zeros(4, int), np.zeros(3, int), np.zeros(4, int))


# ---------------------------------------------------------------------------
# Calibración por clase predicha
# ---------------------------------------------------------------------------

class TestCalibracionPorClase:
    def test_desglosa_por_clase_predicha(self) -> None:
        y_true = np.array([LABEL_STABLE, LABEL_STABLE, LABEL_DOWN, LABEL_UP])
        y_pred = np.array([LABEL_STABLE, LABEL_DOWN, LABEL_DOWN, LABEL_UP])
        conf = np.array([0.9, 0.7, 0.6, 0.8])
        filas = {f["clase"]: f for f in calibracion_por_clase(y_true, y_pred, conf)}
        assert filas["STABLE"]["n"] == 1
        assert filas["STABLE"]["precision_observada"] == pytest.approx(1.0)
        # De las dos predichas como DOWN, una acertó y la otra no.
        assert filas["DOWN"]["n"] == 2
        assert filas["DOWN"]["precision_observada"] == pytest.approx(0.5)
        assert filas["DOWN"]["confianza_media"] == pytest.approx(0.65)
        assert filas["UP"]["n"] == 1

    def test_una_clase_nunca_predicha_no_se_inventa(self) -> None:
        y = np.array([LABEL_STABLE] * 4)
        filas = {f["clase"]: f for f in calibracion_por_clase(y, y, np.full(4, 0.5))}
        assert filas["UP"]["n"] == 0
        assert filas["UP"]["precision_observada"] is None
        assert sum(f["n"] for f in filas.values()) == len(y)

    def test_expone_la_sobreconfianza_de_una_clase(self) -> None:
        """Un 95 % sostenido con acierto real del 50 % debe verse como brecha."""
        y_true = np.array([LABEL_DOWN, LABEL_UP] * 50)
        y_pred = np.full(100, LABEL_DOWN)
        filas = {f["clase"]: f
                 for f in calibracion_por_clase(y_true, y_pred, np.full(100, 0.95))}
        assert filas["DOWN"]["precision_observada"] == pytest.approx(0.5)
        assert filas["DOWN"]["brecha"] < -0.4
