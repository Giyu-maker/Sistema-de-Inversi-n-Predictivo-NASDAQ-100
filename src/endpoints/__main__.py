"""`python -m src.endpoints` — arranca el API de M2 con uvicorn.

Host y puerto por entorno (`ENDPOINTS_HOST`/`ENDPOINTS_PORT`, por defecto
`127.0.0.1:8010`): el loopback por defecto es a propósito, igual que con
InfluxDB y PostgreSQL — el servicio no se expone a la intranet hasta que
alguien lo pida escribiéndolo. En `docker-compose.yml` se sobrescribe a
`0.0.0.0:8000` dentro de la red y solo se publica `127.0.0.1:8010` en el host.
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "src.endpoints.app:app",
        host=os.environ.get("ENDPOINTS_HOST", "127.0.0.1"),
        port=int(os.environ.get("ENDPOINTS_PORT", "8010")),
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
    )


if __name__ == "__main__":
    main()
