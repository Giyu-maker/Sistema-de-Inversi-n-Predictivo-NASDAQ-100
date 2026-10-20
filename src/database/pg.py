"""Conexión a PostgreSQL desde la Máquina 2 — todo por entorno (§15.1).

No hay credenciales, hosts ni rutas escritas en el código:

* `PGDSN` — si existe, se usa tal cual (para quien prefiera una DSN completa).
* Si no, se arma con `PGHOST`/`PGPORT` (por defecto `127.0.0.1:5432`, que es lo
  que publica el contenedor en loopback) más `POSTGRES_DB/USER/PASSWORD`, que
  ya existen desde el Sprint 2.

En `docker-compose.yml` solo se sobrescribe `PGHOST=postgres`: el `.env` no
cambia entre correr en host y correr en contenedor (§15.1).

**Mínimo privilegio (D4):** en esta fase NO se crea un rol de solo lectura; el
API usa las mismas credenciales de usuario que el scheduler y emite únicamente
`SELECT`. Esa deuda queda documentada en `docs/despliegue.md` y en el backlog
para el Sprint 4, junto con RNF-001 (sesión) y RNF-005 (aislamiento).
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import psycopg2
from psycopg2 import extensions


class ErrorDeBaseDatos(RuntimeError):
    """Fallo de acceso a PostgreSQL con un mensaje utilizable en la API."""


def dsn() -> str:
    """DSN de conexión construida desde el entorno."""
    explicito = os.environ.get("PGDSN")
    if explicito:
        return explicito
    host = os.environ.get("PGHOST", "127.0.0.1")
    puerto = os.environ.get("PGPORT", "5432")
    base = os.environ.get("POSTGRES_DB")
    usuario = os.environ.get("POSTGRES_USER")
    if not base or not usuario:
        raise ErrorDeBaseDatos(
            "faltan POSTGRES_DB y POSTGRES_USER (o la variable PGDSN) en el entorno."
        )
    # La contraseña puede venir de PGPASSWORD (libpq) o de POSTGRES_PASSWORD:
    # se aceptan las dos para no duplicar configuración entre host y compose.
    password = os.environ.get("PGPASSWORD") or os.environ.get("POSTGRES_PASSWORD") or ""
    return (
        f"host={host} port={puerto} dbname={base} "
        f"user={usuario} password={password}"
    )


@contextmanager
def conexion(dsn_personalizada: str | None = None) -> Iterator[extensions.connection]:
    """Conexión por operación: abre, confirma o revuelve, y cierra.

    No hay pool porque el ciclo escribe una fila cada 5 minutos y el API solo
    consulta: mantener conexiones vivas entre peticiones costaría más memoria
    que lo que ahorra. Si eso cambia (Sprint 4, más escrituras), el cambio
    afecta solo a este contexto.
    """
    try:
        conn = psycopg2.connect(dsn_personalizada or dsn())
    except psycopg2.OperationalError as exc:
        raise ErrorDeBaseDatos(f"no se pudo conectar a PostgreSQL: {exc}") from exc
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
