"""SyntheticMarketDataProvider — fuente determinista para pruebas unitarias.

Evita golpear la API real (y consumir la cuota diaria de 800 créditos de
Twelve Data) en cada corrida de pytest. Entrega siempre un payload canónico
fijo y conocido, o un error simulado si se le configura.
"""

from __future__ import annotations

import copy
from typing import Any

# Dos velas de ejemplo con las que los tests de orquestador calculan variación.
_DEFAULT_CANDLES: dict[str, dict[str, float]] = {
    "2026-10-06 09:30:00": {
        "open": 100.0, "high": 101.5, "low": 99.5, "close": 101.0, "volume": 900.0,
    },
    "2026-10-06 09:35:00": {
        "open": 101.0, "high": 102.5, "low": 100.5, "close": 102.0, "volume": 1000.0,
    },
}


class SyntheticMarketDataProvider:
    """Entrega velas sintéticas en formato canónico; funciona sin red y sin API key."""

    def __init__(
        self,
        candles: dict[str, dict[str, float]] | None = None,
        error: str | None = None,
    ) -> None:
        """`candles` por defecto usa las velas de ejemplo; `error` simula un fallo de proveedor."""
        self._candles = _DEFAULT_CANDLES if candles is None else candles
        self._error = error

    async def fetch_candles(self) -> dict[str, Any]:
        if self._error is not None:
            return {"error": self._error}
        # deepcopy: un test que muta el payload no contamina la siguiente llamada.
        return {"candles": copy.deepcopy(self._candles)}
