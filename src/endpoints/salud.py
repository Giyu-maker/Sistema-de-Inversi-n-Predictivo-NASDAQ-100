"""`GET /api/health` — liveness del contenedor `api`.

No toca InfluxDB, PostgreSQL ni M1 a propósito: es la comprobación de que el
proceso responde, y si dependiera de todo lo demás el healthcheck del contenedor
reiniciaría el servicio cuando lo que falla es una dependencia. Para ver el
estado real está `/api/status`.
"""

from __future__ import annotations

from fastapi import APIRouter

from src.endpoints.esquemas import HealthSalida

router = APIRouter()


@router.get("/api/health", response_model=HealthSalida, tags=["salud"])
def salud() -> HealthSalida:
    """200 siempre que el proceso esté vivo (healthcheck del contenedor)."""
    return HealthSalida()
