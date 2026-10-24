"""Perfiles de riesgo (RF-403) y la ÚNICA regla de «señal operable» (RF-304).

Dónde vive la regla y por qué aquí
----------------------------------
La pregunta «¿esta predicción se puede operar?» se resuelve **una sola vez**, en
el backend, en este módulo. La API devuelve la respuesta ya decidida
(`senal: {operable, motivo, umbral, perfil}`) y la interfaz solo la pinta: el
SRS §10.1 trata la UI como presentacional. Si la regla viviera en dos lenguajes
habría dos respuestas distintas para la misma predicción, y el día que
discreparan nadie sabría cuál miró el operador.

Nada aquí decide operaciones: esto es la capa **Asesor** (RF-401). El
`RiskManager` del Sprint 4 será quien, en Modo Autónomo, consulte exactamente
esta misma función antes de enviar una orden.

Los tres perfiles y sus umbrales son los de AGENTS.md §8 / SRS RF-403. El umbral
por defecto es el de `MODERATE` (0,75) porque RF-304 y SDD §5.4 lo fijan como
default del sistema.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Tendencia = Literal["UP", "DOWN", "STABLE"]

#: Perfil → confianza mínima para considerar operable una señal (RF-403).
UMBRALES_POR_PERFIL: dict[str, float] = {
    "CONVENTIONAL": 0.85,
    "MODERATE": 0.75,
    "AGGRESSIVE": 0.65,
}

#: El perfil con el que arranca el sistema (RF-304, SDD §5.4).
#: SDD §3.1.1 declara `CONVENTIONAL` como DEFAULT de la columna: la diferencia
#: queda registrada en `docs/change-requests.md` CR-004 y la siembra idempotente
#: escribe explícitamente este valor (`src/database/configuraciones.py`).
PERFIL_POR_DEFECTO = "MODERATE"

TENDENCIAS_VALIDAS: tuple[str, ...] = ("UP", "DOWN", "STABLE")

#: Traducción a lenguaje llano de las tendencias para los textos que lee el
#: operador (AGENTS.md §5.5, brief Fase B2 regla 1). Los campos estructurados
#: (`tendencia`, `trend_prediction`) siguen exigiendo los enums del contrato;
#: esto solo pinta palabras en el `motivo`.
EN_LLANO: dict[str, str] = {
    "UP": "al alza",
    "DOWN": "a la baja",
    "STABLE": "sin movimiento",
}


@dataclass(frozen=True)
class Senal:
    """Veredicto de «señal operable» listo para serializar en la API."""

    operable: bool
    motivo: str
    umbral: float
    perfil: str
    tendencia: str | None
    confianza: float | None


def umbral_de(perfil: str) -> float:
    """Confianza mínima del perfil. `ValueError` si el perfil no existe."""
    try:
        return UMBRALES_POR_PERFIL[perfil]
    except KeyError as exc:
        validos = ", ".join(sorted(UMBRALES_POR_PERFIL))
        raise ValueError(f"perfil de riesgo desconocido: {perfil!r} (válidos: {validos})") from exc


def perfil_valido(perfil: str) -> bool:
    return perfil in UMBRALES_POR_PERFIL


def _numero(valor: float) -> str:
    """0.75 → «0,75»: la interfaz es español neutro y aquí se escribe texto."""
    return f"{valor:.2f}".replace(".", ",")


def evaluar_senal(tendencia: str, confianza: float, perfil: str) -> Senal:
    """Aplica la regla completa de señal operable (RF-304).

    1. `STABLE` **nunca** es señal, con la confianza que sea: es el modelo
       diciendo que no hay movimiento que explotar (informe Sprint 3 §10.3).
    2. Cualquier `UP`/`DOWN` por debajo del umbral del perfil activo tampoco.
    3. Solo pasa lo anterior con `UP`/`DOWN` y confianza ≥ umbral.

    Lanza `ValueError` con tendencia o confianza inválidas: una confianza fuera
    de [0,1] llegando a esta función significa un dato corrupto, y decidir
    «operable» sobre eso sería peor que fallar.
    """
    if tendencia not in TENDENCIAS_VALIDAS:
        raise ValueError(f"tendencia inválida: {tendencia!r} (esperado UP, DOWN o STABLE)")
    if not 0.0 <= confianza <= 1.0:
        raise ValueError(f"confianza fuera de [0, 1]: {confianza!r}")
    umbral = umbral_de(perfil)

    if tendencia == "STABLE":
        return Senal(
            operable=False,
            motivo=(
                "Sin movimiento esperado: el modelo no anticipa movimiento "
                "aprovechable, con cualquier nivel de confianza."
            ),
            umbral=umbral,
            perfil=perfil,
            tendencia=tendencia,
            confianza=confianza,
        )
    if confianza < umbral:
        return Senal(
            operable=False,
            motivo=(
                f"Confianza {_numero(confianza)} por debajo del umbral "
                f"{_numero(umbral)} del perfil {perfil}."
            ),
            umbral=umbral,
            perfil=perfil,
            tendencia=tendencia,
            confianza=confianza,
        )
    return Senal(
        operable=True,
        motivo=(
            f"Señal operable: tendencia {EN_LLANO[tendencia]} con confianza "
            f"{_numero(confianza)} ≥ umbral {_numero(umbral)} del perfil {perfil}."
        ),
        umbral=umbral,
        perfil=perfil,
        tendencia=tendencia,
        confianza=confianza,
    )


def senal_sin_prediccion(perfil: str) -> Senal:
    """Cuando todavía no hay predicción que evaluar (apertura de sesión, BD vacía)."""
    umbral = umbral_de(perfil)
    return Senal(
        operable=False,
        motivo=(
            "Sin predicción registrada: no hay señal que evaluar. "
            f"Umbral del perfil {perfil}: {_numero(umbral)}."
        ),
        umbral=umbral,
        perfil=perfil,
        tendencia=None,
        confianza=None,
    )
