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
from datetime import date, datetime, time, timedelta

from src.utils.fechas import ZONA_COLOMBIA, ZONA_MERCADO

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
        apertura = _proxima_apertura(dia, feriados)
        extra = f" · {_frase_reapertura(apertura)}" if apertura else ""
        return EstadoMercado(abierto=False, motivo=f"Fin de semana{extra}")
    if dia in feriados:
        apertura = _proxima_apertura(dia, feriados)
        extra = f" · {_frase_reapertura(apertura)}" if apertura else ""
        return EstadoMercado(
            abierto=False,
            motivo=f"Feriado NYSE ({dia.strftime('%d/%m/%Y')}){extra}",
            feriado=True,
        )
    if local.time() < APERTURA:
        # P2.2: la frase aporta la hora real de apertura en las dos zonas.
        hoy = datetime.combine(dia, APERTURA, tzinfo=ZONA_MERCADO)
        col = hoy.astimezone(ZONA_COLOMBIA).strftime("%H:%M")
        return EstadoMercado(
            abierto=False,
            motivo=f"Antes de la apertura: hoy a las 09:30 ET ({col} hora de Colombia)",
        )
    if local.time() >= CIERRE:
        cierre = datetime.combine(dia, CIERRE, tzinfo=ZONA_MERCADO)
        col = cierre.astimezone(ZONA_COLOMBIA).strftime("%H:%M")
        apertura = _proxima_apertura(dia, feriados)
        extra = f" · {_frase_reapertura(apertura)}" if apertura else ""
        return EstadoMercado(
            abierto=False,
            motivo=f"Después del cierre (16:00 ET, {col} hora de Colombia){extra}",
        )
    return EstadoMercado(abierto=True, motivo="Horario regular de mercado")


# -- frase de reapertura (P2.2): calculada con la fecha real, nunca a mano -----

_DIAS_SEMANA_ES = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


def _proxima_apertura(dia: date, feriados: frozenset[date]) -> datetime | None:
    """09:30 ET del próximo día hábil de la NYSE después de `dia`."""
    for atras in range(1, 9):
        candidato = dia + timedelta(days=atras)
        if candidato.weekday() < 5 and candidato not in feriados:
            return datetime.combine(candidato, APERTURA, tzinfo=ZONA_MERCADO)
    return None


def _frase_reapertura(apertura: datetime) -> str:
    """«reabre el lunes 02/11 a las 09:30 ET (08:30 hora de Colombia)».

    La hora de Colombia se deriva del instante (EDT/EST lo resuelve la zona),
    así que el 01/11 el texto solo cambia de 08:30 a 09:30 solo.
    """
    nombre = _DIAS_SEMANA_ES[apertura.weekday()]
    col = apertura.astimezone(ZONA_COLOMBIA).strftime("%H:%M")
    return f"reabre el {nombre} {apertura.strftime('%d/%m')} a las 09:30 ET ({col} hora de Colombia)"


# -- ventana de consulta de la fuente (P1.9) -----------------------------------

#: Margen tras el cierre para capturar la última vela del día: la ventana de
#: consulta de la fuente termina a las 16:10 ET, no en el cierre exacto.
MARGEN_CIERRE = time(16, 10)


def ahora_ny() -> datetime:
    """Reloj actual en la zona de la sesión: el ÚNICO reloj de sesión (P1.9).

    Toda decisión de «¿es momento de consultar la fuente?» se toma con este
    reloj (hora de Nueva York); la hora de Colombia solo se usa para mostrar.
    """
    return datetime.now(ZONA_MERCADO)


def ventana_de_consulta(
    ahora: datetime, feriados: frozenset[date] = frozenset()
) -> str:
    """«¿Vale la pena consultar la fuente ahora?» → `sesion` | `cierre` | `ninguna`.

    De lunes a viernes no feriados: `sesion` entre 09:30 y las 16:10 ET
    (sesión regular + margen de cierre para la última vela), `cierre` el
    resto del día (recuperación de huecos, una sola vez) y `ninguna` antes
    de abrir o en fin de semana/feriado. `ahora` se espera en hora de
    Nueva York (`ahora_ny()`); con EST/EDT la pared local manda, así que el
    cambio de hora no requiere ajuste (P1.9 + cruce 30/10→02/11 testado).
    """
    if ahora.weekday() >= 5 or ahora.date() in feriados:
        return "ninguna"
    minutos = ahora.hour * 60 + ahora.minute
    apertura = APERTURA.hour * 60 + APERTURA.minute
    margen = MARGEN_CIERRE.hour * 60 + MARGEN_CIERRE.minute
    if apertura <= minutos < margen:
        return "sesion"
    if minutos >= margen:
        return "cierre"
    return "ninguna"


# -- feriados NYSE: lista mínima + configurable (P1.9) --------------------------

#: Días en que la NYSE cierra (lista mínima; el resto de los festivos
#: federales —Columbus y Veterans— NO cierran y por eso no están).
#: Se añaden los de `FERIADOS_NYSE` vía `feriados_operativos()`.


def _pascua(anio: int) -> date:
    """Domingo de Pascua (Meeus/Jones/Butcher) para el Good Friday."""
    a = anio % 19
    b = anio // 100
    c = anio % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    base = h + l - 7 * m + 114
    return date(anio, base // 31, base % 31 + 1)


def _cuarto_jueves(anio: int, mes: int) -> date:
    """Cuarto jueves de `mes` (Acción de Gracias)."""
    d = date(anio, mes, 1)
    d += timedelta(days=(3 - d.weekday()) % 7)  # primer jueves
    return d + timedelta(days=21)


def _complementario_lunes(anio: int, mes: int) -> date:
    """Primer lunes de `mes` (MLK, presidentes, trabajo)."""
    d = date(anio, mes, 1)
    return d + timedelta(days=(7 - d.weekday()) % 7)


def _ultimo_lunes(anio: int, mes: int) -> date:
    """Último lunes de `mes` (Memorial Day)."""
    d = date(anio, mes, 31)
    while d.weekday() != 0:
        d -= timedelta(days=1)
    return d


def _observado(d: date) -> date:
    """Sábado → viernes anterior; domingo → lunes siguiente (regla de la NYSE)."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def feriados_nyse_minimos(anio: int) -> frozenset[date]:
    """Clausuras de la NYSE para `anio` (lista mínima, sin festivos abiertos)."""
    pascua = _pascua(anio)
    return frozenset(
        {
            _observado(date(anio, 1, 1)),      # Año Nuevo
            _complementario_lunes(anio, 1) + timedelta(days=14),  # MLK (3.er lunes de enero)
            _complementario_lunes(anio, 2) + timedelta(days=14),  # Presidentes (3.er lunes)
            pascua - timedelta(days=2),        # Good Friday
            _ultimo_lunes(anio, 5),            # Memorial Day
            _observado(date(anio, 6, 19)),     # Juneteenth
            _observado(date(anio, 7, 4)),      # Independencia
            _complementario_lunes(anio, 9),    # Día del Trabajo
            _cuarto_jueves(anio, 11),          # Acción de Gracias
            _observado(date(anio, 12, 25)),    # Navidad
        }
    )


def feriados_operativos() -> frozenset[date]:
    """Feriados que apagan la consulta a la fuente: mínimos ∪ `FERIADOS_NYSE`.

    La lista base es la de `feriados_nyse_minimos` del año en curso (más el
    siguiente Año Nuevo, por si el año gira cerca de medianoche); el entorno
    solo puede AÑADIR días (`FERIADOS_NYSE=YYYY-MM-DD,…`).
    """
    hoy = datetime.now(ZONA_MERCADO).date()
    base = set(feriados_nyse_minimos(hoy.year)) | set(feriados_nyse_minimos(hoy.year + 1))
    return frozenset(base | set(feriados_desde_entorno()))


# -- la fecha de sesión vive SOLO aquí (revisión, punto 3) ---------------------
#: Formato de las etiquetas de hora que habla Twelve Data (siempre NY).
_FMT_API = "%Y-%m-%d %H:%M:%S"


def apertura_ms(dia: date) -> int:
    """Epoch ms de la apertura (09:30 ET) de `dia`, con el DST resuelto.

    Es el ORIGEN de los 78 slots de la sesión: la detección de huecos, el
    número de slots esperados y cualquier cálculo de fecha de sesión pasan
    por aquí — jamás por un offset fijo ni por la hora de Colombia (que es
    solo para mostrar). En EDT son las 13:30 UTC; en EST, las 14:30.
    """
    return int(datetime.combine(dia, APERTURA, tzinfo=ZONA_MERCADO).timestamp() * 1000)


def etiqueta_ny(epoch_ms: int) -> str:
    """Epoch ms → ``YYYY-MM-DD HH:MM:SS`` en hora de Nueva York.

    La ÚNICA traducción epoch→etiqueta de la sesión (la API de Twelve Data y
    los informes la usan; la hora de Colombia jamás se coloca aquí).
    """
    return datetime.fromtimestamp(epoch_ms / 1000, tz=ZONA_MERCADO).strftime(_FMT_API)
