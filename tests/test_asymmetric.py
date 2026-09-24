"""Pruebas unitarias del motor asimétrico — RF-201 (2x) y RF-202 (límite 1.25%)."""

from __future__ import annotations

import pytest

from src.calculator.asymmetric import AsymmetricCalculator


class TestCalculateYield:
    """Ganancia apalancada 2x y passthrough de pérdidas (RF-201)."""

    def test_ganancia_positiva_se_duplica(self) -> None:
        assert AsymmetricCalculator.calculate_yield(2.5) == pytest.approx(5.0)

    def test_ganancia_positiva_pequena_se_duplica(self) -> None:
        assert AsymmetricCalculator.calculate_yield(0.75) == pytest.approx(1.5)

    def test_variacion_cero_da_cero(self) -> None:
        assert AsymmetricCalculator.calculate_yield(0.0) == 0.0

    def test_perdida_pasa_sin_apalancar(self) -> None:
        assert AsymmetricCalculator.calculate_yield(-1.5) == pytest.approx(-1.5)


class TestIsThresholdBreached:
    """Evaluación del umbral de pérdida del 1.25% (RF-202)."""

    def test_perdida_dentro_del_limite_no_dispara(self) -> None:
        # -1.00% → dentro del límite
        assert AsymmetricCalculator.is_threshold_breached(10000.0, 9900.0) is False

    def test_perdida_justo_dentro_del_limite_no_dispara(self) -> None:
        # -1.24% → un pelo dentro del límite
        assert AsymmetricCalculator.is_threshold_breached(10000.0, 9876.0) is False

    def test_perdida_que_rompe_el_limite_dispara(self) -> None:
        # -2.00% → supera el 1.25%
        assert AsymmetricCalculator.is_threshold_breached(10000.0, 9800.0) is True

    def test_perdida_justo_sobre_el_limite_dispara(self) -> None:
        # -1.26% → un pelo sobre el límite
        assert AsymmetricCalculator.is_threshold_breached(10000.0, 9874.0) is True

    def test_caso_borde_exacto_menos_1_25_dispara(self) -> None:
        # -1.25% exacto → sí dispara (comparación <= -1.25 del SDD §5.2.2)
        assert AsymmetricCalculator.is_threshold_breached(10000.0, 9875.0) is True

    def test_precio_de_entrada_invalido_lanza_error(self) -> None:
        with pytest.raises(ValueError):
            AsymmetricCalculator.is_threshold_breached(0.0, 100.0)
        with pytest.raises(ValueError):
            AsymmetricCalculator.is_threshold_breached(-1.0, 100.0)
