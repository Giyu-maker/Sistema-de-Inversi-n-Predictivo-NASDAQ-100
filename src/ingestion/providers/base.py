"""Contrato `MarketDataProvider` — toda fuente de velas de 5M implementa esta interfaz.

Formato canónico (contrato interno entre proveedores, `IngestionEngine` y el
orquestador; ningún componente del pipeline conoce el formato nativo de un
proveedor concreto):

- Éxito::

      {"candles": {"2026-10-06 15:55:00": {"open": 1.0, "high": 1.1, "low": 0.9,
                                            "close": 1.05, "volume": 1000.0}, ...}}

  Timestamps en hora de Nueva York con formato ``YYYY-MM-DD HH:MM:SS``; el
  orden del dict es irrelevante, `IngestionEngine` ordena cronológicamente.

- Error (cuota agotada, clave inválida, payload malformado)::

      {"error": "<motivo en español neutro>"}

  `IngestionEngine.validate_payload()` devuelve ``False`` y el ciclo se
  descarta sin persistir.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MarketDataProvider(Protocol):
    """Interfaz estructural (Protocol de typing): basta con implementar el método."""

    async def fetch_candles(self) -> dict[str, Any]:
        """Obtiene las velas de 5M recientes en el formato canónico.

        Las fallas de red/HTTP se propagan como excepción (el orquestador las
        captura y reintenta en el siguiente ciclo de 5 min).
        """
        ...
