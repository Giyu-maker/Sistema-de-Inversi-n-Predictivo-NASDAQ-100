"""Recuperación de huecos en la ingesta de velas de 5M (hallazgo B2.5).

Hallazgo real del cierre de B2.4: con el scheduler caído o arrancando a
media sesión, en InfluxDB quedaban días con UNA sola vela — el 22 y el
23/10/2026 tenían 1 vela de las 78 del horario regular — y sin ventana de
12 velas `predecir_y_persistir` no genera predicciones. El tick normal solo
escribe la última vela del payload, así que ese hueco se cerraba solo si la
sesión entera volvía a recorrerse.

Regla (B2.5): **al arrancar y en cada tick**, si la última vela del bucket
está a MÁS de un intervalo (5 min) respecto al reloj **dentro de la sesión
actual**, se pide el tramo faltante a `MarketDataProvider`
(`fetch_candles_range`, 1 crédito) y se escribe con la guarda
insert-if-absent de CR-003 (`write_candles_batch`): repetir la pasada es
idempotente.

Cuota del plan gratuito Basic de Twelve Data (8 créditos/min, 800/día):
cada tramo cuesta 1 crédito y `CuotaBasica` no deja pedir cuando la ventana
correspondiente está llena — el siguiente tick reintenta. Agotar la cuota
diaria dejaría además al scheduler sin datos hasta medianoche UTC, así que
no es solo cortesía: es fiabilidad.

Fuera de sesión no se hace nada (motivo `fuera-de-sesion`): de 16:00 a
09:30 no llegan ticks por definición, y pedir tramos de horas cerradas
gastaría créditos en un rango que el proveedor no devuelve.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from src.ingestion.engine import IngestionEngine, parse_raw_candle
from src.utils.mercado import APERTURA, mercado

_NY = ZoneInfo("America/New_York")
_FORMATO_API = "%Y-%m-%d %H:%M:%S"
#: Un tramo de sesión son como mucho 78 velas: SIEMPRE cabe en una petición.
INTERVALO_MS = 5 * 60 * 1000


class CuotaBasica:
    """Control deslizante de créditos: 8/min y 800/día (plan Basic de Twelve Data).

    `reloj` es inyectable para tests y devuelve epoch ms. El día se cuenta en
    UTC (así rige el límite diario del proveedor); la ventana de minuto es
    deslizante sobre los últimos 60 s.
    """

    def __init__(
        self,
        por_minuto: int = 8,
        por_dia: int = 800,
        reloj: Callable[[], float] | None = None,
    ) -> None:
        self.por_minuto = por_minuto
        self.por_dia = por_dia
        self._reloj: Callable[[], float] = reloj or (lambda: time.time() * 1000)
        self._consumos: list[float] = []  # epoch ms de cada crédito consumido

    def puede_consumir(self) -> bool:
        """True si queda hueco en AMBAS ventanas (minuto y día UTC)."""
        ahora = self._reloj()
        minuto = sum(1 for t in self._consumos if t > ahora - 60_000)
        hoy = datetime.fromtimestamp(ahora / 1000, tz=UTC).date()
        dia = sum(
            1
            for t in self._consumos
            if datetime.fromtimestamp(t / 1000, tz=UTC).date() == hoy
        )
        return minuto < self.por_minuto and dia < self.por_dia

    def consumir(self) -> None:
        """Registra el crédito de una petición ya hecha."""
        ahora = self._reloj()
        self._consumos.append(ahora)
        # Poda: más allá de 24 h ningún consumo puede afectar a la ventana de
        # 60 s ni al día UTC en curso (mantiene la lista acotada).
        self._consumos = [t for t in self._consumos if t > ahora - 86_400_000]


@dataclass(frozen=True)
class InformeHueco:
    """Resultado de una pasada de recuperación (para el log y los tests)."""

    #: `recuperado` | `sin-hueco` | `fuera-de-sesion` | `sin-datos` |
    #: `sin-cuota` | `sin-rango` | `payload-invalido`
    motivo: str
    pedidas: int = 0       # peticiones a la API (créditos consumidos)
    escritas: int = 0
    omitidas: int = 0      # ya existentes (guarda CR-003)
    detalle: str = ""


def _ny(epoch_ms: int) -> str:
    """Epoch ms → ``YYYY-MM-DD HH:MM:SS`` en hora de Nueva York (formato de la API)."""
    return datetime.fromtimestamp(epoch_ms / 1000, tz=_NY).strftime(_FORMATO_API)


async def recuperar_hueco(
    engine: IngestionEngine,
    cuota: CuotaBasica,
    *,
    ahora: datetime | None = None,
    feriados: frozenset[date] = frozenset(),
) -> InformeHueco:
    """Cierra el hueco entre la última vela persistida y el reloj de sesión.

    Una sola petición por pasada como máximo (1 crédito); si algo falla se
    devuelve un informe con el motivo y el ciclo normal del tick sigue.
    """
    instante = ahora or datetime.now(_NY)
    estado = mercado(instante, feriados)
    if not estado.abierto:
        return InformeHueco("fuera-de-sesion", detalle=estado.motivo)

    ultima = await engine.ultima_vela_ms()
    if ultima is None:
        return InformeHueco(
            "sin-datos",
            detalle="el bucket no tiene velas: eso es trabajo del backfill",
        )

    ahora_ms = int(instante.timestamp() * 1000)
    if ahora_ms - ultima <= INTERVALO_MS:
        return InformeHueco("sin-hueco")

    # El tramo empieza en la vela siguiente a la última guardada, pero nunca
    # antes de la apertura de HOY: lo de ayer es trabajo del backfill y el
    # plan solo cubre el horario regular.
    apertura_hoy = datetime.combine(instante.date(), APERTURA, tzinfo=_NY)
    inicio = max(ultima + INTERVALO_MS, int(apertura_hoy.timestamp() * 1000))
    if inicio >= ahora_ms:
        return InformeHueco("sin-rango", detalle="el hueco no llega a un intervalo completo")

    if not cuota.puede_consumir():
        return InformeHueco(
            "sin-cuota",
            detalle="8/min o 800/día del plan Basic agotados: se reintenta en el siguiente tick",
        )

    payload = await engine.provider.fetch_candles_range(
        start_date=_ny(inicio), end_date=_ny(ahora_ms)
    )
    cuota.consumir()

    if "error" in payload:
        return InformeHueco("payload-invalido", pedidas=1, detalle=str(payload["error"]))
    series = payload.get("candles")
    if not isinstance(series, dict) or not series:
        return InformeHueco(
            "sin-rango",
            pedidas=1,
            detalle="la API no devolvió velas para el tramo solicitado",
        )
    try:
        velas = [parse_raw_candle(ts, raw) for ts, raw in series.items()]
    except ValueError as exc:  # incluye pydantic.ValidationError
        return InformeHueco("payload-invalido", pedidas=1, detalle=str(exc))

    escritas, omitidas = await engine.write_candles_batch(velas)
    return InformeHueco(
        "recuperado",
        pedidas=1,
        escritas=escritas,
        omitidas=omitidas,
        detalle=f"{_ny(inicio)} → {_ny(ahora_ms)} ({len(velas)} velas pedidas)",
    )
