"""Horario de mercado — la regla de estado solo aplica DURANTE sesión.

Corrección acordada en el plan B1: fuera de 09:30–16:00 ET los ticks no llegan
por definición, así que «dato desactualizado» sería un falso positivo de 17
horas cada día.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.utils.fechas import ZONA_MERCADO
from src.utils.mercado import feriados_desde_entorno, mercado


def _en_et(hora: int, minuto: int = 0, *, dia: int = 16, mes: int = 10) -> datetime:
    """Instante en hora de Nueva York (viernes 16/10/2026 por defecto)."""
    return datetime(2026, mes, dia, hora, minuto, tzinfo=ZONA_MERCADO)


class TestHorarioRegular:
    def test_dentro_de_la_sesion_esta_abierto(self) -> None:
        estado = mercado(_en_et(11, 0))
        assert estado.abierto is True
        assert estado.feriado is False
        assert estado.horario == "09:30-16:00 ET"

    @pytest.mark.parametrize(
        "hora,minuto,palabra",
        [(9, 0, "apertura"), (16, 0, "cierre"), (17, 45, "cierre")],
    )
    def test_fuera_de_limites_cierra_y_lo_dice(
        self, hora: int, minuto: int, palabra: str
    ) -> None:
        estado = mercado(_en_et(hora, minuto))
        assert estado.abierto is False
        assert palabra in estado.motivo

    @pytest.mark.parametrize("hora,minuto", [(9, 30), (15, 59)])
    def test_los_extremos_de_la_sesion_quedan_abiertos(
        self, hora: int, minuto: int
    ) -> None:
        # 09:30 inclusive y 15:59 inclusive: la apertura cuenta y el último
        # minuto antes del cierre también.
        estado = mercado(_en_et(hora, minuto))
        assert estado.abierto is True
        assert estado.motivo == "Horario regular de mercado"

    @pytest.mark.parametrize("dia", [17, 18])  # sábado y domingo
    def test_fin_de_semana_cierra_el_estado(self, dia: int) -> None:
        estado = mercado(_en_et(11, 0, dia=dia))
        assert estado.abierto is False
        assert estado.motivo == "Fin de semana"
        assert estado.feriado is False


class TestFeriados:
    def test_feriado_configurado_cierra_y_lo_dice(self) -> None:
        feriados = frozenset({date(2026, 11, 26)})
        estado = mercado(_en_et(11, 0, dia=26, mes=11), feriados)
        assert estado.abierto is False
        assert estado.feriado is True
        assert "Feriado NYSE" in estado.motivo
        assert "26/11/2026" in estado.motivo

    def test_sin_feriados_el_mismo_dia_queda_abierto(self) -> None:
        # Es el caso «sin lista»: sin feriados configurados el sistema no
        # distingue un feriado de un día sin datos, y esa incertidumbre se
        # traduce después en «sin datos en horario de mercado».
        assert mercado(_en_et(11, 0, dia=26, mes=11)).abierto is True

    def test_feriado_mal_escrito_se_ignora_y_no_tumba_el_servicio(self) -> None:
        assert feriados_desde_entorno("26-11-2026, no-una-fecha") == frozenset()
        assert feriados_desde_entorno("2026-11-26") == frozenset({date(2026, 11, 26)})
        assert feriados_desde_entorno("") == frozenset()

    def test_lee_la_variable_de_entorno_cuando_no_le_llega_nada(self, monkeypatch) -> None:
        monkeypatch.setenv("FERIADOS_NYSE", "2026-12-25, 2027-01-01")
        assert feriados_desde_entorno() == frozenset({date(2026, 12, 25), date(2027, 1, 1)})


class TestGuardas:
    def test_hora_ingenua_es_error_no_una_conversion(self) -> None:
        # Convertir una hora sin zona a la de Nueva York es adivinar, y adivinar
        # mal deja el sistema diciendo «cerrado» en plena sesión.
        with pytest.raises(ValueError, match="zona horaria"):
            mercado(datetime(2026, 10, 16, 11, 0))

    def test_acepta_utc_y_lo_convierte_a_la_hora_del_mercado(self) -> None:
        # 15:00 UTC = 11:00 EDT: dentro de la sesión.
        assert mercado(datetime(2026, 10, 16, 15, 0, tzinfo=timezone.utc)).abierto is True
