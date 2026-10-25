"""Horario de mercado — la regla de estado solo aplica DURANTE sesión.

Corrección acordada en el plan B1: fuera de 09:30–16:00 ET los ticks no llegan
por definición, así que «dato desactualizado» sería un falso positivo de 17
horas cada día.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.utils.fechas import ZONA_MERCADO
from src.utils.mercado import (
    feriados_desde_entorno,
    feriados_nyse_minimos,
    mercado,
    ventana_de_consulta,
)


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


def _ny(texto: str) -> datetime:
    """ISO sin zona → instante en la zona de la sesión (America/New_York)."""
    return datetime.fromisoformat(texto).replace(tzinfo=ZONA_MERCADO)


class TestVentanaDeConsulta:
    """P1.9: la fuente solo se consulta L–V 09:30–16:10 ET, hora de NY."""

    def test_sesion_de_verano_con_margen_de_cierre(self) -> None:
        assert ventana_de_consulta(_ny("2026-10-20T11:00")) == "sesion"
        assert ventana_de_consulta(_ny("2026-10-20T16:05")) == "sesion"  # última vela
        assert ventana_de_consulta(_ny("2026-10-20T16:15")) == "cierre"
        assert ventana_de_consulta(_ny("2026-10-20T08:00")) == "ninguna"

    def test_sesion_de_invierno_tras_el_cambio_de_hora(self) -> None:
        # Posterior al 01/11/2026 (EST): la pared local de NY manda.
        assert ventana_de_consulta(_ny("2026-11-03T11:00")) == "sesion"
        assert ventana_de_consulta(_ny("2026-11-03T16:20")) == "cierre"

    def test_fin_de_semana_y_feriado_no_consultan(self) -> None:
        assert ventana_de_consulta(_ny("2026-10-24T12:00")) == "ninguna"  # sábado
        assert ventana_de_consulta(_ny("2026-10-25T12:00")) == "ninguna"  # domingo
        feriados = feriados_nyse_minimos(2026)
        accion = _ny("2026-11-26T11:00")  # Acción de Gracias
        assert ventana_de_consulta(accion, feriados) == "ninguna"

    def test_cruce_del_cambio_de_hora_vie_30_10_a_lun_02_11(self) -> None:
        # Viernes en EDT y lunes en EST: la ventana no se desplaza ni un minuto.
        assert ventana_de_consulta(_ny("2026-10-30T09:35")) == "sesion"
        assert ventana_de_consulta(_ny("2026-10-30T16:05")) == "sesion"
        assert ventana_de_consulta(_ny("2026-11-01T12:00")) == "ninguna"  # gira el reloj
        assert ventana_de_consulta(_ny("2026-11-02T09:35")) == "sesion"
        assert ventana_de_consulta(_ny("2026-11-02T16:15")) == "cierre"


class TestFeriadosNyseMinimos:
    """Lista mínima de cierres de la NYSE (P1.9), configurable por entorno."""

    def test_dias_conocidos_de_2026(self) -> None:
        f = feriados_nyse_minimos(2026)
        assert date(2026, 1, 1) in f    # Año Nuevo
        assert date(2026, 1, 19) in f   # MLK (3.er lunes)
        assert date(2026, 2, 16) in f   # Presidentes (3.er lunes)
        assert date(2026, 4, 3) in f    # Good Friday (Pascua 05/04)
        assert date(2026, 5, 25) in f   # Memorial (último lunes)
        assert date(2026, 7, 3) in f    # 4 de julio cae sábado → viernes
        assert date(2026, 9, 7) in f    # Trabajo (1.er lunes)
        assert date(2026, 11, 26) in f  # Acción de Gracias (4.º jueves)
        assert date(2026, 12, 25) in f  # Navidad

    def test_dias_que_la_nyse_abre_no_estan(self) -> None:
        f = feriados_nyse_minimos(2026)
        assert date(2026, 10, 12) not in f  # Columbus Day: la NYSE abre
        assert date(2026, 11, 11) not in f  # Veterans Day: la NYSE abre
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
