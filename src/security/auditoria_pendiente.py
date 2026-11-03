"""Auditoría en archivo: el respaldo append-only cuando PostgreSQL no está.

Por qué existe
--------------
La regla 3 del SDD §7 obliga a auditar antes de actuar, pero el Kill-Switch
**no puede depender de la auditoría ni de la base de datos**: un botón de
pánico que esperara a PostgreSQL no sería un botón de pánico. La decisión
aprobada para ese camino es:

    (a) bandera en memoria → (b) compare-and-set en BD (mejor esfuerzo)
    → (c) liquidación → (d) auditoría (mejor esfuerzo)

Si (d) falla, el evento no se pierde: queda en este archivo y se vuelve a
volcar después (al arrancar y tras cada escritura exitosa). El resto de las
acciones —órdenes, cambio de modo, reinicio— sigue con la lectura estricta:
sin auditoría, no se ejecutan.

Formato y permisos
------------------
JSONL: una línea por evento, con el fallo que lo mandó aquí. Se escribe con
`O_APPEND` en una sola llamada a `write`, que en Linux es un apéndice atómico
para tamaños razonables, y con permiso 0600: es un registro de quién pulsó el
botón de emergencia.

El bloqueo (`fcntl`) protege el volcado, que reescribe el archivo: sin él, dos
procesos volcando a la vez podrían tirar una línea recién añadida. Donde no
hay `fcntl` (Windows) se sigue intentando sin bloqueo: es mejor esfuerzo, y
por eso el módulo importa flojamente.

Qué NO garantiza
----------------
Que el archivo sea transaccional con la base: si el volcado se corta a mitad,
pueden quedar líneas duplicadas en `audit_logs`. Un registro duplicado es
menos malo que uno perdido, y por eso se prefiere así.
"""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator

from src.security.auditoria import EventoAuditoria, RepositorioAuditoria

logger = logging.getLogger(__name__)

#: Ruta por defecto, relativa al directorio de trabajo del servicio. Se
#: cambia por entorno (§15.1) cuando el servicio corre en un contenedor y
#: `logs/` tiene que caer en un volumen montado.
RUTA_POR_DEFECTO = Path("logs") / "auditoria-pendiente.jsonl"


class ColaAuditoriaPendiente:
    """Archivo JSONL con la auditoría que no pudo escribirse en PostgreSQL."""

    def __init__(self, ruta: Path | str | None = None) -> None:
        self.ruta = Path(ruta) if ruta is not None else RUTA_POR_DEFECTO

    # -- escritura ---------------------------------------------------------

    def registrar(self, evento: EventoAuditoria, fallo: str) -> bool:
        """Añade el evento al archivo. True si quedó escrito.

        Si el archivo tampoco se puede escribir (disco lleno, permisos), el
        fallo va al log del servicio y la acción **no se aborta**: es el
        último escalón del mejor esfuerzo.
        """
        # Se estampa el momento aquí: `registrar` de la base lo haría con su
        # reloj y el evento perdería la hora real del disparo.
        con_tiempo = evento if evento.timestamp_ms is not None else replace(
            evento, timestamp_ms=_ahora_ms()
        )
        registro = {
            "evento": con_tiempo.a_dict(),
            "fallo": fallo,
            "registrado_en": _ahora_ms(),
        }
        linea = (json.dumps(registro, ensure_ascii=False, default=str) + "\n").encode("utf-8")
        try:
            self.ruta.parent.mkdir(parents=True, exist_ok=True)
            with self._bloqueo():
                descriptor = os.open(
                    self.ruta, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
                )
                try:
                    # Una sola escritura: en Linux el apéndice es atómico.
                    os.write(descriptor, linea)
                finally:
                    os.close(descriptor)
        except Exception as exc:  # noqa: BLE001 — el último escalón no puede tumbar nada
            logger.error(
                "No se pudo escribir la auditoría pendiente en %s: %s", self.ruta, exc
            )
            return False
        return True

    # -- lectura -----------------------------------------------------------

    def leer(self) -> list[dict[str, Any]]:
        """Registros del archivo. Líneas ilegibles se omiten (no se pierde lo demás)."""
        if not self.ruta.exists():
            return []
        try:
            texto = self.ruta.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.error("No se pudo leer la auditoría pendiente (%s): %s", self.ruta, exc)
            return []
        registros: list[dict[str, Any]] = []
        for linea in texto.splitlines():
            if not linea.strip():
                continue
            try:
                datos = json.loads(linea)
            except json.JSONDecodeError:
                logger.warning("Línea ilegible en %s omitida.", self.ruta)
                continue
            if isinstance(datos, dict):
                registros.append(datos)
        return registros

    def pendientes(self) -> int:
        """Cuántos eventos esperan volcarse (0 = archivo ausente o vacío)."""
        return len(self.leer())

    # -- volcado -----------------------------------------------------------

    def volcar_pendientes(self, repo: RepositorioAuditoria) -> int:
        """Inserta lo que haya en el archivo y reescribe solo lo que faltó.

        Devuelve cuántos eventos entraron en la base. Los que fallen siguen
        en el archivo para el próximo intento.
        """
        registros = self.leer()
        if not registros:
            return 0
        with self._bloqueo():
            # Se relee dentro del bloqueo: entre la primera lectura y aquí
            # pudo llegar otro evento, y reescribir sin mirar lo tiraría.
            registros = self.leer()
            if not registros:
                return 0
            quedan: list[dict[str, Any]] = []
            volcadas = 0
            for datos in registros:
                try:
                    evento = EventoAuditoria.desde_dict(datos["evento"])
                    repo.registrar(evento)
                    volcadas += 1
                except Exception as exc:  # noqa: BLE001 — una línea no puede frenar al resto
                    logger.warning("No se pudo volcar un evento pendiente: %s", exc)
                    quedan.append(datos)
            self._reescribir(quedan)
        if volcadas:
            logger.info("Auditoría pendiente volcada: %d evento(s).", volcadas)
        return volcadas

    def _reescribir(self, registros: list[dict[str, Any]]) -> None:
        """Reescribe el archivo de forma atómica (temporal + rename)."""
        temporal = self.ruta.with_name(self.ruta.name + ".tmp")
        contenido = "".join(
            json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in registros
        )
        try:
            descriptor = os.open(temporal, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            try:
                os.write(descriptor, contenido.encode("utf-8"))
            finally:
                os.close(descriptor)
            os.replace(temporal, self.ruta)
        except Exception as exc:  # noqa: BLE001
            logger.error("No se pudo reescribir la auditoría pendiente: %s", exc)

    @contextmanager
    def _bloqueo(self) -> Iterator[None]:
        """Bloqueo exclusivo entre procesos; sin `fcntl` se sigue sin él."""
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        archivo_lock = self.ruta.with_name(self.ruta.name + ".lock")
        descriptor = os.open(archivo_lock, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX)
            except ImportError:  # pragma: no cover — solo en plataformas sin fcntl
                pass
            yield
        finally:
            os.close(descriptor)


def _ahora_ms() -> int:
    return int(time.time() * 1000)
