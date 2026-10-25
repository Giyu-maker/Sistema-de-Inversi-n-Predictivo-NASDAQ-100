"""Recuperación de huecos en la ingesta de velas de 5M (hallazgo B2.5).

Hallazgo real del cierre de B2.4: con el scheduler caído o arrancando a
media sesión, en InfluxDB quedaban días con UNA sola vela — el 22 y el
23/10/2026 tenían 1 vela de las 78 del horario regular — y sin ventana de
12 velas `predecir_y_persistir` no genera predicciones. El tick normal solo
escribe la última vela del payload, así que ese hueco se cerraba solo si la
sesión entera volvía a recorrerse.

Regla (B2.5, endurecida para la sesión del lunes): **al arrancar y en cada
tick**, para cada sesión candidata — la **última sesión cerrada** (hasta
`MIRADA_ATRAS_DIAS` días atrás, así cubre el fin de semana) y, si el
mercado está en horario regular, la de **hoy**— se calculan las velas
esperadas (los `SLOTS_SESION` slots de 5 min de 09:30 a 16:00 ET; parciales
para la sesión en curso) y se comparan con las existentes en InfluxDB. Si
falta alguna — **huecos internos incluidos**: no solo el tramo posterior a
la última vela— se pide a `MarketDataProvider` el tramo que va de la
primera vela faltante al fin del tramo (como mucho 1 crédito por sesión) y
se escribe con la guarda insert-if-absent de CR-003
(`write_candles_batch`): repetir la pasada es idempotente.

Las candidatas se recorren de la más antigua a la más reciente: primero se
cierra la sesión anterior —es la que alimenta la ventana de 12 velas— y
después la de hoy. Sin candidatas (p. ej. una semana de feriados) el
motivo es `fuera-de-sesion`; con el bucket vacío, `sin-datos` (trabajo del
backfill, no del tick).

Cuota del plan gratuito Basic de Twelve Data (8 créditos/min, 800/día):
cada tramo cuesta 1 crédito y `CuotaBasica` no deja pedir cuando la ventana
correspondiente está llena — el siguiente tick reintenta. Agotar la cuota
diaria dejaría además al scheduler sin datos hasta medianoche UTC, así que
no es solo cortesía: es fiabilidad.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from src.ingestion.engine import IngestionEngine, parse_raw_candle
from src.utils.mercado import apertura_ms, ahora_ny, etiqueta_ny, mercado

INTERVALO_MS = 5 * 60 * 1000
#: Horario regular 09:30–16:00 ET en velas de 5 min: 78 slots por sesión.
SLOTS_SESION = 78
#: Hasta cuántos días hacia atrás se mira la última sesión cerrada candidata.
MIRADA_ATRAS_DIAS = 7


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


def _sesion_valida(dia: date, feriados: frozenset[date]) -> bool:
    return dia.weekday() < 5 and dia not in feriados


def _sesiones_candidatas(
    instante: datetime, feriados: frozenset[date]
) -> list[tuple[date, bool]]:
    """Sesiones a auditar, de la más antigua a la más reciente.

    Cada par es `(día, en_curso)`: la última sesión cerrada dentro de la
    mirada hacia atrás y —solo si hoy es día válido y ya pasó la apertura—
    la de hoy, aún en curso.
    """
    hoy = instante.date()
    candidatas: list[tuple[date, bool]] = []
    for retroceso in range(1, MIRADA_ATRAS_DIAS + 1):
        dia = hoy - timedelta(days=retroceso)
        if _sesion_valida(dia, feriados):
            candidatas.append((dia, False))
            break
    if (
        _sesion_valida(hoy, feriados)
        and instante.timestamp() * 1000 >= apertura_ms(hoy)
    ):
        candidatas.append((hoy, True))
    return candidatas


def _slots_esperados(dia: date, en_curso: bool, ahora_ms: int) -> list[int]:
    """Los `SLOTS_SESION` inicios de vela esperados; en curso, solo los ya transcurridos."""
    base = apertura_ms(dia)
    slots = [base + k * INTERVALO_MS for k in range(SLOTS_SESION)]
    if en_curso:
        # Solo velas cuya ventana de 5 min YA cerró: la vela en formación la
        # escribe el tick normal y no debe contar como hueco.
        slots = [s for s in slots if s + INTERVALO_MS <= ahora_ms]
    return slots


async def recuperar_hueco(
    engine: IngestionEngine,
    cuota: CuotaBasica,
    *,
    ahora: datetime | None = None,
    feriados: frozenset[date] = frozenset(),
) -> InformeHueco:
    """Cierra los huecos de las sesiones candidatas (interna y cerrada incluidas).

    Como mucho una petición por sesión candidata (2 créditos en el peor caso,
    siempre dentro de la cuota). Si algo falla se devuelve un informe con el
    motivo y el ciclo normal del tick sigue.
    """
    instante = ahora or ahora_ny()
    candidatas = _sesiones_candidatas(instante, feriados)
    if not candidatas:
        return InformeHueco(
            "fuera-de-sesion",
            detalle=f"ningún día válido en los últimos {MIRADA_ATRAS_DIAS} días",
        )

    ultima = await engine.ultima_vela_ms()
    if ultima is None:
        return InformeHueco(
            "sin-datos",
            detalle="el bucket no tiene velas: eso es trabajo del backfill",
        )

    ahora_ms = int(instante.timestamp() * 1000)
    pedidas = escritas = omitidas = 0
    recuperaciones: list[str] = []
    fallo: InformeHueco | None = None

    for dia, en_curso in candidatas:
        esperados = _slots_esperados(dia, en_curso, ahora_ms)
        if not esperados:
            continue  # la sesión de hoy aún no completa ni una vela
        existentes = await engine.existing_timestamps(esperados)
        faltantes = sorted(set(esperados) - existentes)
        if not faltantes:
            continue  # esa sesión está completa

        if not cuota.puede_consumir():
            fallo = InformeHueco(
                "sin-cuota",
                pedidas=pedidas,
                escritas=escritas,
                omitidas=omitidas,
                detalle="8/min o 800/día del plan Basic agotados: se reintenta en el siguiente tick",
            )
            break

        # Cerrada: el límite superior pasa por detrás de la última vela
        # esperada; en curso: hasta ahora mismo (recupera también la vela en
        # formación, que la guarda insert-if-absent no duplica).
        fin_tramo = ahora_ms if en_curso else faltantes[-1] + INTERVALO_MS
        payload = await engine.provider.fetch_candles_range(
            start_date=etiqueta_ny(faltantes[0]), end_date=etiqueta_ny(fin_tramo)
        )
        cuota.consumir()
        pedidas += 1

        if "error" in payload:
            fallo = InformeHueco(
                "payload-invalido", pedidas=pedidas, escritas=escritas,
                omitidas=omitidas,
                detalle=f"{dia.strftime('%d/%m/%Y')}: {payload['error']}",
            )
            continue
        series = payload.get("candles")
        if not isinstance(series, dict) or not series:
            fallo = InformeHueco(
                "sin-rango", pedidas=pedidas, escritas=escritas, omitidas=omitidas,
                detalle=f"la API no devolvió velas para el {dia.strftime('%d/%m/%Y')}",
            )
            continue
        try:
            velas = [parse_raw_candle(ts, raw) for ts, raw in series.items()]
        except ValueError as exc:  # incluye pydantic.ValidationError
            fallo = InformeHueco(
                "payload-invalido", pedidas=pedidas, escritas=escritas,
                omitidas=omitidas, detalle=f"{dia.strftime('%d/%m/%Y')}: {exc}",
            )
            continue

        e, o = await engine.write_candles_batch(velas)
        escritas += e
        omitidas += o
        recuperaciones.append(
            f"{dia.strftime('%d/%m/%Y')}: {etiqueta_ny(faltantes[0])} → {etiqueta_ny(fin_tramo)} "
            f"({len(faltantes)} huecos, {len(velas)} velas)"
        )

    if recuperaciones:
        detalle = "; ".join(recuperaciones)
        if fallo is not None and fallo.motivo == "sin-cuota":
            detalle += f"; {fallo.detalle}"
        return InformeHueco(
            "recuperado", pedidas=pedidas, escritas=escritas,
            omitidas=omitidas, detalle=detalle,
        )
    if fallo is not None:
        return fallo
    return InformeHueco("sin-hueco", pedidas=pedidas)
