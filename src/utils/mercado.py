"""Horario de mercado — la regla de estado solo evalúa el dato DURANTE sesión.

Corrección del plan B1: la antigüedad de una vela de 5 minutos no significa nada
cuando el mercado está cerrado (fuera de 09:30–16:00 ET los ticks no llegan por
definición). Sin esta pieza, cada noche el estado se quedaría en amarillo por
«dato desactualizado» durante 17 horas, que es un estado falso: lo que pasa es
que el mercado está cerrado.

Regla aplicada por `src/endpoints/estado.py`:

* **Dentro de sesión** → se evalúan retraso, IA y predicción (verde/amarillo).
* **Fuera de sesión** → estado neutro `CERRADO`, «Mercado cerrado».

Feriados NYSE: lista mínima **configurable** por `FERIADOS_NYSE` (fechas
`YYYY-MM-DD` separadas por coma). Con la lista puesta se distingue «hoy es
feriado» de «hoy no llegan datos». Sin ella, el feriado desconocido se reporta
con el mensaje «sin datos en horario de mercado», que es exactamente lo que se
sabe: mercado abierto según el calendario y ningún dato nuevo.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, time

from src.utils.fechas import ZONA_MERCADO

logger = logging.getLogger(__name__)

#: Horario regular de la sesión (SRS §3.1: el pre/after-hours queda fuera de
#: esta regla porque el plan gratuito de datos solo cubre el horario regular).
APERTURA = time(9, 30)
CIERRE = time(16, 0)
HORARIO_REGULAR_TXT = "09:30-16:00 ET"


@dataclass(frozen=True)
class EstadoMercado:
    """Si el mercado está abierto y por qué."""

    abierto: bool
    motivo: str
    #: True si el motivo es un feriado configurado (para distinguirlo de un
    #: fin de semana o de un simple fuera de horario).
    feriado: bool = False
    zona: str = "America/New_York"
    horario: str = HORARIO_REGULAR_TXT


def feriados_desde_entorno(valor: str | None = None) -> frozenset[date]:
    """Lee `FERIADOS_NYSE` y devuelve los feriados válidos.

    Una fecha mal escrita no puede tumbar el servicio: se ignora con aviso. El
    efecto de ignorarla es volver al caso «sin lista», que es el comportamiento
    por defecto documentado más arriba.
    """
    texto = os.environ.get("FERIADOS_NYSE", "") if valor is None else valor
    feriados: set[date] = set()
    for trozo in texto.split(","):
        trozo = trozo.strip()
        if not trozo:
            continue
        try:
            feriados.add(date.fromisoformat(trozo))
        except ValueError:
            logger.warning(
                "Feriado NYSE ignorado por formato inválido %r (se espera YYYY-MM-DD).", trozo
            )
    return frozenset(feriados)


def mercado(ahora: datetime, feriados: frozenset[date] = frozenset()) -> EstadoMercado:
    """Estado del mercado para un instante **consciente de zona horaria**.

    Lanza `ValueError` con hora ingenua: convertir una hora sin zona a la hora
    de Nueva York es adivinar, y adivinar mal deja el sistema diciendo
    «cerrado» en plena sesión.
    """
    if ahora.tzinfo is None:
        raise ValueError("ahora debe llevar zona horaria (aware); ver src.utils.fechas")
    local = ahora.astimezone(ZONA_MERCADO)
    dia = local.date()

    if local.weekday() >= 5:
        return EstadoMercado(abierto=False, motivo="Fin de semana")
    if dia in feriados:
        return EstadoMercado(
            abierto=False, motivo=f"Feriado NYSE ({dia.strftime('%d/%m/%Y')})", feriado=True
        )
    if local.time() < APERTURA:
        return EstadoMercado(
            abierto=False, motivo=f"Antes de la apertura ({HORARIO_REGULAR_TXT})"
        )
    if local.time() >= CIERRE:
        return EstadoMercado(abierto=False, motivo=f"Después del cierre ({HORARIO_REGULAR_TXT})")
    return EstadoMercado(abierto=True, motivo="Horario regular de mercado")
