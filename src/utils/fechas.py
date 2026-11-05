"""Fechas y zonas horarias — convención `DD/MM/YYYY HH:mm:ss` (SRS §5.6).

Una sola implementación para que la API, los logs y el frontend hablen el mismo
idioma. El dashboard mostrará la hora de Colombia como principal y la de Nueva
York en el detalle (Fase B): por eso aquí conviven las dos zonas en lugar de
escribir `strftime` en cada endpoint.

Las zonas se resuelven con `zoneinfo` del standard library. Sin `tzdata` (sistema
o paquete) el import fallaría con un error críptico en la imagen `slim` de
Docker, por eso `tzdata` está en las dependencias base: es ~400 KB y evita que
un contenedor lean mal la hora de mercado.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

#: Hora local del operador (Sala de trading, Colombia).
ZONA_COLOMBIA = ZoneInfo("America/Bogota")
#: Hora del mercado: el horario regular y los feriados se definen en Nueva York.
ZONA_MERCADO = ZoneInfo("America/New_York")

#: Formato obligatorio de todo el sistema (AGENTS.md §5.6).
FORMATO = "%d/%m/%Y %H:%M:%S"


def fecha_hora(ms: int | None, zona: ZoneInfo = ZONA_COLOMBIA) -> str:
    """Epoch ms → `DD/MM/YYYY HH:mm:ss` en la zona pedida.

    `None` devuelve cadena vacía: un registro sin fecha se ve como vacío, no
    como 01/01/1970, que es un error que se confunde con un dato real.
    """
    if ms is None:
        return ""
    instante = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return instante.astimezone(zona).strftime(FORMATO)


def fecha_iso_utc(ms: int) -> str:
    """Epoch ms → RFC3339 en UTC, que es lo que entiende Flux."""
    instante = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return instante.isoformat().replace("+00:00", "Z")


def fecha_ny(epoch_ms: int) -> str:
    """Epoch ms → `DD/MM/YYYY` en hora de Nueva York.

    Es la fecha con la que se identifica una SESIÓN (el mercado abre a las
    09:30 ET, no a la hora de Colombia): los reportes y el visor la usan
    para agrupar y filtrar.
    """
    instante = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
    return instante.astimezone(ZONA_MERCADO).strftime("%d/%m/%Y")


def hora_ny(epoch_ms: int) -> str:
    """Epoch ms → `HH:MM` en hora de Nueva York (hora de la vela, ET)."""
    instante = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
    return instante.astimezone(ZONA_MERCADO).strftime("%H:%M")


def fecha_de_epoch_ny(epoch_ms: int) -> date:
    """Fecha de sesión (ET) de un epoch ms, para agrupar por sesión."""
    instante = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
    return instante.astimezone(ZONA_MERCADO).date()
