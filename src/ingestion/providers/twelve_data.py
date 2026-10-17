"""TwelveDataProvider — Twelve Data API (`time_series`, velas de 5M).

Adaptador de la API de Twelve Data al formato canónico descrito en
`src/ingestion/providers/base.py`.

⚠ **Uso académico/personal** (documentado también en la CR de cambio de
fuente): el plan gratuito *Basic* de Twelve Data está licenciado para uso
"personal, internal y non-commercial". Este sistema se desarrolla como
ejercicio de curso sin actividad comercial real, por lo que el plan gratuito
es suficiente. **Si este sistema se llevara a producción real requeriría
contratar un plan comercial licenciado de Twelve Data** (o de otro
proveedor) — no ocultarlo es una decisión consciente, no un descuido.

Cuota del plan Basic: 8 créditos/min y 800/día, cubre holgadamente las ~192
velas diarias que exige RF-101 (muestreo cada 5 min de 4:00 AM a 8:00 PM EST).
"""

from __future__ import annotations

from typing import Any

import httpx


class TwelveDataProvider:
    """Cliente asíncrono de Twelve Data que normaliza su respuesta al formato canónico."""

    BASE_URL = "https://api.twelvedata.com/time_series"
    _REQUEST_TIMEOUT_S = 10.0

    def __init__(self, api_key: str, symbol: str) -> None:
        self.api_key = api_key
        self.symbol = symbol

    async def fetch_candles(self) -> dict[str, Any]:
        """Consulta las velas de 5M del horario regular y las normaliza.

        Límites verificados empíricamente el 06/10/2026 en el plan gratuito
        Basic (documentar en la CR de fuente):
        - `prepost=true` devuelve HTTP 403: las ventanas pre/after-hours están
          reservadas al plan Pro+ → Basic cubre solo 09:30–16:00 ET (78 velas
          de las ~192 que pide el escenario de RF-101). Si el sistema exigiera
          cobertura 4:00–20:00 EST haría falta un plan superior u otra fuente.
        - Los `datetime` llegan en hora local de Nueva York sin offset (el
          campo `meta.timezone` viene nulo): `IngestionEngine` los interpreta
          como America/New_York, consistente con lo observado (09:30–15:55).
        """
        return await self._time_series(outputsize="50")

    async def fetch_candles_range(
        self,
        start_date: str,
        end_date: str,
        outputsize: int = 5000,
    ) -> dict[str, Any]:
        """Obtiene un tramo de histórico para el backfill (no forma parte de RF-101).

        Límites del plan gratuito Basic verificados en la documentación oficial
        de Twelve Data:
        - `outputsize` admite **1–5000** puntos por petición (5000 es el máximo).
        - Cada `/time_series` cuesta **1 crédito** → 8/min y 800/día.
        - El intradía está disponible **desde 2022** para acciones/ETF de EE. UU.
        - `start_date`/`end_date` acotan el rango y `outputsize` determina
          cuántas filas se devuelven **dentro** de ese rango (las más recientes).

        `start_date` y `end_date` van en formato ``YYYY-MM-DD HH:MM:SS`` en hora
        local del intercambio (Nueva York), consistente con los `datetime` que
        devuelve la API. Cada ventana devuelve hasta `outputsize` velas; el
        paginado hacia atrás lo resuelve `src/ingestion/backfill.py` moviendo el
        `end_date` a la vela más antigua recibida.
        """
        return await self._time_series(
            outputsize=str(outputsize), start_date=start_date, end_date=end_date
        )

    async def _time_series(self, **extra_params: str) -> dict[str, Any]:
        params: dict[str, str] = {
            "symbol": self.symbol,
            "interval": "5min",
            "apikey": self.api_key,
            **extra_params,
        }
        async with httpx.AsyncClient(timeout=self._REQUEST_TIMEOUT_S) as client:
            response = await client.get(self.BASE_URL, params=params)
            response.raise_for_status()
            payload: Any = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Respuesta inesperada de Twelve Data: se esperaba un objeto JSON.")
        return self._normalize(payload)

    @staticmethod
    def _normalize(data: dict[str, Any]) -> dict[str, Any]:
        """Traduce la respuesta nativa de Twelve Data al formato canónico.

        Éxito nativo: ``{"meta": ..., "values": [{"datetime": ..., "open": ...}, ...]}``
        (el más reciente primero). Error nativo: ``{"code": ..., "message": ...}``.
        """
        values = data.get("values")
        if isinstance(values, list):
            candles: dict[str, dict[str, float]] = {}
            for row in values:
                if not isinstance(row, dict):
                    return {"error": "Fila no válida en la respuesta de Twelve Data."}
                try:
                    candles[str(row["datetime"])] = {
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": float(row["volume"]),
                    }
                except (KeyError, TypeError, ValueError) as exc:
                    # Vela malformada: se invalida el payload completo (RF-102);
                    # el siguiente ciclo de 5 min trae datos frescos.
                    return {"error": f"Vela malformada en la respuesta de Twelve Data: {exc}"}
            return {"candles": candles}
        message = data.get("message") or data.get("error") or f"Respuesta sin datos: {data}"
        return {"error": str(message)}
