"""Regla de «señal operable» — RF-304 y RF-403 (decisión D7).

La regla vive SOLO en el backend y la API la devuelve decidida: estos tests son
la garantía de que esa única implementación hace lo que AGENTS.md §8 dice. La
interfaz no vuelve a calcularla (SRS §10.1).
"""

from __future__ import annotations

import pytest

from src.security.perfiles import (
    PERFIL_POR_DEFECTO,
    UMBRALES_POR_PERFIL,
    evaluar_senal,
    perfil_valido,
    senal_sin_prediccion,
    umbral_de,
)


class TestUmbrales:
    def test_los_tres_perfiles_son_los_del_agents(self) -> None:
        # AGENTS.md §8 / RF-403: 0,85 · 0,75 · 0,65.
        assert UMBRALES_POR_PERFIL == {
            "CONVENTIONAL": 0.85,
            "MODERATE": 0.75,
            "AGGRESSIVE": 0.65,
        }

    def test_el_perfil_por_defecto_es_moderate_por_rf304(self) -> None:
        # RF-304 y SDD §5.4 fijan 0,75 como umbral por defecto del sistema.
        assert PERFIL_POR_DEFECTO == "MODERATE"
        assert umbral_de(PERFIL_POR_DEFECTO) == 0.75

    def test_perfil_desconocido_es_error_explicito(self) -> None:
        with pytest.raises(ValueError, match="perfil de riesgo desconocido"):
            umbral_de("LOCO")
        assert perfil_valido("LOCO") is False
        assert perfil_valido("AGGRESSIVE") is True


class TestStableNuncaEsSenal:
    @pytest.mark.parametrize("confianza", [0.0, 0.5, 0.75, 0.99, 1.0])
    def test_stable_no_es_operable_con_ninguna_confianza(self, confianza: float) -> None:
        # El modelo diciendo «no pasa nada» no es una decisión que explotar,
        # por muy seguro que esté de ello (informe Sprint 3 §10.3).
        for perfil in UMBRALES_POR_PERFIL:
            senal = evaluar_senal("STABLE", confianza, perfil)
            assert senal.operable is False, f"STABLE operable con {perfil} y {confianza}"
            assert senal.tendencia == "STABLE"
            assert "Sin movimiento esperado" in senal.motivo


class TestUmbralDeConfianza:
    @pytest.mark.parametrize("perfil,umbral", sorted(UMBRALES_POR_PERFIL.items()))
    def test_por_debajo_del_umbral_no_es_operable(self, perfil: str, umbral: float) -> None:
        senal = evaluar_senal("UP", round(umbral - 0.01, 4), perfil)
        assert senal.operable is False
        assert senal.umbral == umbral
        # El motivo dice el porcentaje (español) y el nombre en español del
        # perfil (RF-403): el operador debe poder leer por qué no, sin mirar
        # el código ni traducir MODERATE.
        umbral_escrito = f"{umbral * 100:.0f} %"
        assert f"umbral {umbral_escrito}" in senal.motivo
        nombre = {"CONVENTIONAL": "Conservador", "MODERATE": "Moderado", "AGGRESSIVE": "Agresivo"}
        assert nombre[perfil] in senal.motivo

    @pytest.mark.parametrize("perfil,umbral", sorted(UMBRALES_POR_PERFIL.items()))
    def test_en_el_umbral_y_encima_es_operable(self, perfil: str, umbral: float) -> None:
        for confianza in (umbral, round(umbral + 0.01, 4), 1.0):
            for tendencia in ("UP", "DOWN"):
                senal = evaluar_senal(tendencia, confianza, perfil)
                assert senal.operable is True
                assert senal.tendencia == tendencia
                assert senal.confianza == confianza
                assert senal.motivo.startswith("Señal operable")

    def test_el_mismo_dato_cambia_de_veredicto_según_el_perfil(self) -> None:
        # 0,80: pasa en AGGRESSIVE y MODERATE, no en CONVENTIONAL. Es la prueba
        # de que el umbral manda y no hay un corte escondido en otro sitio.
        assert evaluar_senal("DOWN", 0.80, "AGGRESSIVE").operable is True
        assert evaluar_senal("DOWN", 0.80, "MODERATE").operable is True
        assert evaluar_senal("DOWN", 0.80, "CONVENTIONAL").operable is False


class TestEntradasInvalidas:
    def test_tendencia_invalida_es_error(self) -> None:
        with pytest.raises(ValueError, match="tendencia inválida"):
            evaluar_senal("SUBE", 0.9, "MODERATE")

    @pytest.mark.parametrize("confianza", [-0.1, 1.5, float("nan")])
    def test_confianza_fuera_de_rango_es_error(self, confianza: float) -> None:
        with pytest.raises(ValueError, match="confianza"):
            evaluar_senal("UP", confianza, "MODERATE")


class TestSenalSinPrediccion:
    def test_no_es_operable_y_no_inventa_tendencia(self) -> None:
        senal = senal_sin_prediccion("MODERATE")
        assert senal.operable is False
        assert senal.tendencia is None
        assert senal.confianza is None
        assert senal.umbral == 0.75
        assert "Sin predicción registrada" in senal.motivo
