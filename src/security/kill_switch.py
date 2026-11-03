"""KillSwitchEngine — bloqueo PRIMERO, liquidación DESPUÉS (SDD §5.4, DD-04).

El orden importa y no es una preferencia
----------------------------------------
La decisión aprobada para el Sprint 4 (ajuste 2 del plan) fija cuatro pasos:

    (a) bandera en memoria del proceso — el bloqueo es inmediato y no puede
        depender de nadie;
    (b) compare-and-set en PostgreSQL — mejor esfuerzo;
    (c) liquidación de las posiciones por `BrokerGateway`;
    (d) auditoría — mejor esfuerzo, con respaldo en archivo.

La regla estricta de SDD §7 regla 3 («sin auditoría no hay acción») **no**
aplica aquí: un botón de pánico que esperara a la base de datos no sería un
botón de pánico. Sí aplica —y sin excepciones— al resto de acciones: órdenes,
cambio de modo y reinicio.

Idempotencia
------------
Pulsar dos veces no liquidó dos veces. La garantía sale del compare-and-set
de `RepositorioKillSwitch.activar()`: si la fila ya estaba activa, esta
llamada no gana y no se liquida. Si la base no responde, manda la bandera de
este proceso — la misma que impide el doble disparo en un solo proceso — y el
evento queda registrado con `persistencia_ok = false`.

Interpretación de RNF-010 («prioridad de hilos de CPU»)
-------------------------------------------------------
Literalmente no aplica en Python: no hay prioridades de hilo utilizables sin
ser root, y el GIL reparte el tiempo entre los hilos de forma independiente
de su urgencia. Lo que sí se hace, y es lo que RNF-010 pide en la práctica:

1. el camino de activación no tiene ni un `await` ni una llamada a la base
   antes de poner la bandera;
2. cualquier cosa que emite órdenes consulta la bandera **antes** de cualquier
   cálculo (`RiskManager.decidir`);
3. las tareas de orden pendientes se cancelan al activarse;
4. se miden pulsación→bloqueo y pulsación→liquidación.

Los números salen en `ResultadoDisparo` y en el `payload` del evento de
auditoría, y se reportan en `docs/informe-sprint4.md`.

Bandera por proceso
-------------------
API y scheduler son procesos distintos: cada uno tiene su bandera. La verdad
compartida es la fila de la base, y por eso `esta_bloqueado()` devuelve
`None` —no `False`— cuando no se puede leer: ante la duda, no se emiten órdenes.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from src.database.kill_switch import EstadoKillSwitch, RepositorioKillSwitch
from src.integration.broker import BrokerGateway
from src.security.auditoria import (
    EventoAuditoria,
    RepositorioAuditoria,
    auditar_sincrono,
)
from src.security.auditoria_pendiente import ColaAuditoriaPendiente
from src.utils.fechas import fecha_hora

logger = logging.getLogger(__name__)

#: `action` del evento de auditoría de un disparo (espejo de SDD §3.1.1).
ACCION_DISPARO = "KILL_SWITCH_TRIGGERED"

#: `action` del evento de un reinicio autorizado con la clave (RF-503).
ACCION_REINICIO = "KILL_SWITCH_RESET"

#: Estados de la liquidación en lenguaje llano (AGENTS.md §5.5).
_EN_LLANO: dict[str, str] = {
    "NINGUNA": "sin liquidar",
    "PENDIENTE": "pendiente",
    "EN_CURSO": "en curso",
    "COMPLETADA": "completada",
    "FALLIDA": "fallida",
}


class BanderaBloqueo:
    """Bandera de bloqueo en memoria, por proceso y hilo-segura.

    Es lo que corta **antes que nada** (paso (a)). Solo se pone: el camino de
    activación no hace I/O antes de llamar a `activar()`, que es lo único que
    RNF-010 exige de verdad de esta pieza.
    """

    def __init__(self) -> None:
        self._candado = threading.Lock()
        self._activo = False

    @property
    def activo(self) -> bool:
        with self._candado:
            return self._activo

    def activar(self) -> bool:
        """Pone la bandera. True solo si ESTA llamada fue la que la puso."""
        with self._candado:
            if self._activo:
                return False
            self._activo = True
            return True

    def liberar(self) -> bool:
        """Quita la bandera. True si estaba puesta.

        Solo lo llaman el reinicio autorizado (C3) y los tests: liberar sin
        haber reiniciado la base dejaría el sistema operable en la interfaz y
        bloqueado en la BD.
        """
        with self._candado:
            if not self._activo:
                return False
            self._activo = False
            return True


#: Instancia compartida por todos los módulos de este proceso.
BLOQUEO = BanderaBloqueo()


@dataclass(frozen=True)
class ResumenKillSwitch:
    """Vista de una sola lectura para la API: estado + texto llano.

    `activo` puede ser `None`: «no se pudo verificar» es distinto de «no está
    activo» y quien lo consume no debe traducirlo en operable.
    """

    activo: bool | None
    motivo: str | None
    origen: str | None
    liquidacion_estado: str
    liquidaciones: int
    disparado_en: int | None
    reinicio_en: int | None
    detalle: str  #: lenguaje llano para el operador, calculado AQUÍ


@dataclass(frozen=True)
class ResultadoDisparo:
    """Lo que pasó en un disparo, para la respuesta, la auditoría y el informe."""

    primera_vez: bool
    ms_bloqueo: float
    ms_liquidacion: float
    ordenes: int
    liquidacion_estado: str
    persistencia_ok: bool
    auditoria_ok: bool
    detalle: str  #: lenguaje llano para el operador


class KillSwitchEngine:
    """Ejecuta la liquidación global y administra el bloqueo (SDD §5.4)."""

    def __init__(
        self,
        repo: RepositorioKillSwitch,
        broker: BrokerGateway,
        auditor: RepositorioAuditoria,
        *,
        cola: ColaAuditoriaPendiente | None = None,
        banda: BanderaBloqueo | None = None,
        ahora_ms: Callable[[], int] | None = None,
    ) -> None:
        self._repo = repo
        self._broker = broker
        self._auditor = auditor
        self._cola = cola if cola is not None else ColaAuditoriaPendiente()
        self._banda = banda if banda is not None else BLOQUEO
        self._ahora_ms = ahora_ms

    def _ahora(self) -> int:
        if self._ahora_ms is not None:
            return self._ahora_ms()
        return int(time.time() * 1000)

    # -- consulta ----------------------------------------------------------

    def esta_bloqueado(self) -> bool | None:
        """True / False, o **None** si no se pudo determinar (falla cerrado).

        `None` no es «no bloqueado»: es «no lo sé», y quien llama no emite
        órdenes con un `None`.
        """
        try:
            estado = self._repo.leer()
        except Exception as exc:  # noqa: BLE001 — PostgreSQL caído ⇒ None
            logger.error("No se pudo leer el estado del Kill-Switch: %s", exc)
            return None
        if estado is None:
            return None
        return estado.activo or self._banda.activo

    def estado(self) -> EstadoKillSwitch | None:
        """Fila vigente de `kill_switch_state` (None si no hay)."""
        try:
            return self._repo.leer()
        except Exception as exc:  # noqa: BLE001
            logger.error("No se pudo leer el estado del Kill-Switch: %s", exc)
            return None

    def resumen(self) -> ResumenKillSwitch:
        """Estado + texto llano para la UI, en una sola lectura.

        Los tres casos se distinguen a propósito: activo, inactivo y **no
        verificado**. Los textos se calculan aquí, no en la interfaz, para
        que el panel pinte exactamente lo que el backend decidió.
        """
        estado = self.estado()
        banda = self._banda.activo

        if estado is None:
            if banda:
                return ResumenKillSwitch(
                    activo=True,
                    motivo="Bloqueo en memoria (la base de datos no responde)",
                    origen=None,
                    liquidacion_estado="PENDIENTE",
                    liquidaciones=0,
                    disparado_en=None,
                    reinicio_en=None,
                    detalle=(
                        "Kill-Switch ACTIVO en este proceso y sin poder guardarse en "
                        "la base de datos. No se emitirán órdenes ni sugerencias "
                        "hasta un reinicio autorizado."
                    ),
                )
            return ResumenKillSwitch(
                activo=None,
                motivo=None,
                origen=None,
                liquidacion_estado="NINGUNA",
                liquidaciones=0,
                disparado_en=None,
                reinicio_en=None,
                detalle=(
                    "No se pudo verificar el estado del Kill-Switch: hasta "
                    "confirmarlo no se emitirán órdenes ni sugerencias."
                ),
            )

        if not (estado.activo or banda):
            return ResumenKillSwitch(
                activo=False,
                motivo=estado.motivo,
                origen=estado.origen,
                liquidacion_estado=estado.liquidacion_estado,
                liquidaciones=estado.liquidaciones,
                disparado_en=estado.disparado_en or None,
                reinicio_en=estado.reinicio_en,
                detalle="Sin bloqueo: el Kill-Switch está desactivado.",
            )

        liquidacion = _EN_LLANO.get(estado.liquidacion_estado, estado.liquidacion_estado)
        detalle = (
            f"Kill-Switch ACTIVO desde {fecha_hora(estado.disparado_en)}: "
            f"{estado.motivo}. Liquidación {liquidacion} "
            f"({estado.liquidaciones} registrada(s)). "
            "No se emitirán órdenes ni sugerencias hasta un reinicio autorizado."
        )
        return ResumenKillSwitch(
            activo=True,
            motivo=estado.motivo,
            origen=estado.origen,
            liquidacion_estado=estado.liquidacion_estado,
            liquidaciones=estado.liquidaciones,
            disparado_en=estado.disparado_en or None,
            reinicio_en=estado.reinicio_en,
            detalle=detalle,
        )

    # -- disparo -----------------------------------------------------------

    def execute_global_liquidation(
        self,
        *,
        motivo: str,
        origen: str,
        actor: str,
        ip_address: str,
    ) -> ResultadoDisparo:
        """Dispara el Kill-Switch en el orden aprobado: (a) bandera, (b) BD,
        (c) liquidación, (d) auditoría.

        `primera_vez = False` significa que ya estaba activo: se responde sin
        liquidar de nuevo (idempotencia, RF-501).
        """
        inicio = time.monotonic()

        # (a) Bandera en memoria: sin I/O, sin await, antes que nada.
        memoria_nueva = self._banda.activar()
        ms_bloqueo = (time.monotonic() - inicio) * 1000.0

        # (b) Compare-and-set en la base: mejor esfuerzo.
        persistencia_ok = True
        primera_vez = memoria_nueva
        try:
            primera_vez = self._repo.activar(
                motivo=motivo, origen=origen, actor=actor, ahora_ms=self._ahora()
            )
        except Exception as exc:  # noqa: BLE001 — la base no puede frenar el pánico
            persistencia_ok = False
            logger.error("No se pudo persistir el bloqueo del Kill-Switch: %s", exc)

        if not primera_vez:
            # Ya estaba activo (en la base, o en este proceso): no se liquida
            # dos veces. La bandera ya está puesta, así que el bloqueo sigue.
            return ResultadoDisparo(
                primera_vez=False,
                ms_bloqueo=ms_bloqueo,
                ms_liquidacion=0.0,
                ordenes=0,
                liquidacion_estado="NINGUNA",
                persistencia_ok=persistencia_ok,
                auditoria_ok=True,
                detalle="El Kill-Switch ya estaba activo: no se repite la liquidación.",
            )

        # (c) Liquidación.
        if persistencia_ok:
            persistencia_ok = self._marcar("EN_CURSO", None)
        inicio_liquidacion = time.monotonic()
        ordenes = 0
        fallo: str | None = None
        try:
            ordenes = len(self._broker.liquidar_todo())
        except Exception as exc:  # noqa: BLE001 — el broker puede estar caído
            fallo = str(exc)
            logger.error("La liquidación global falló: %s", exc)
        ms_liquidacion = (time.monotonic() - inicio_liquidacion) * 1000.0
        estado_liquidacion = "FALLIDA" if fallo else "COMPLETADA"
        if persistencia_ok:
            persistencia_ok = self._marcar(estado_liquidacion, self._ahora())

        # (d) Auditoría: mejor esfuerzo, con respaldo en archivo.
        payload: dict[str, Any] = {
            "motivo": motivo,
            "origen": origen,
            "persistencia_ok": persistencia_ok,
            "ms_bloqueo": round(ms_bloqueo, 3),
            "ms_liquidacion": round(ms_liquidacion, 3),
            "ordenes_simuladas": ordenes,
            "liquidacion_estado": estado_liquidacion,
        }
        if fallo:
            payload["fallo_liquidacion"] = fallo
        evento = EventoAuditoria(
            action=ACCION_DISPARO,
            actor=actor,
            ip_address=ip_address,
            payload=payload,
            timestamp_ms=self._ahora(),
        )
        auditoria_ok = True
        try:
            auditar_sincrono(self._auditor, evento)
        except Exception as exc:  # noqa: BLE001 — mejor esfuerzo, paso (d)
            auditoria_ok = False
            self._cola.registrar(evento, str(exc))

        if fallo:
            detalle = (
                f"Kill-Switch activo. La liquidación falló: {fallo}. "
                "No se emitirán órdenes hasta un reinicio autorizado."
            )
        else:
            detalle = (
                f"Kill-Switch activo. Se liquidaron {ordenes} posición(es) "
                "simulada(s); no se emitirán órdenes ni sugerencias hasta un "
                "reinicio autorizado."
            )
        if not persistencia_ok:
            detalle += " El bloqueo no pudo guardarse en la base de datos."
        if not auditoria_ok:
            detalle += " El registro de auditoría quedó pendiente de volcado."

        return ResultadoDisparo(
            primera_vez=True,
            ms_bloqueo=ms_bloqueo,
            ms_liquidacion=ms_liquidacion,
            ordenes=ordenes,
            liquidacion_estado=estado_liquidacion,
            persistencia_ok=persistencia_ok,
            auditoria_ok=auditoria_ok,
            detalle=detalle,
        )

    def _marcar(self, estado: str, ahora_ms: int | None) -> bool:
        """Actualiza el estado de la liquidación. False si no se pudo (mejor esfuerzo)."""
        try:
            self._repo.marcar_liquidacion(estado, ahora_ms)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("No se pudo marcar la liquidación como %s: %s", estado, exc)
            return False

    # -- reinicio autorizado (RF-503) --------------------------------------

    def registrar_intento_fallido(self, ahora_ms: int) -> int | None:
        """+1 al contador de intentos de la clave. None si no estaba activo."""
        try:
            return self._repo.registrar_intento_fallido(ahora_ms)
        except Exception as exc:  # noqa: BLE001
            logger.error("No se pudo registrar el intento de reinicio: %s", exc)
            return None

    def reiniciar(
        self, *, actor: str, ip_address: str, ahora_ms: int, sesion: date
    ) -> bool:
        """Reinicio autorizado (RF-503). **Auditoría estricta antes de la acción.**

        A diferencia del disparo, aquí sí gana la regla 3 del SDD §7: quien no
        puede registrar el reinicio no puede autorizarlo, y la excepción sube
        al caller (el endpoint contesta 503 y el sistema sigue bloqueado).

        `True` si esta llamada fue la que desactivó (idempotente).
        """
        evento = EventoAuditoria(
            action=ACCION_REINICIO,
            actor=actor,
            ip_address=ip_address,
            payload={"fase": "solicitud", "sesion": sesion.isoformat()},
            timestamp_ms=ahora_ms,
        )
        auditar_sincrono(self._auditor, evento)
        desactivado = self._repo.reiniciar(ahora_ms=ahora_ms, sesion=sesion)
        if desactivado:
            # La bandera del proceso se quita solo después de que la base
            # confirmó: liberar antes dejaría la UI en «operable» con la fila
            # todavía activa.
            self._banda.liberar()
        return desactivado

    # -- recuperación ------------------------------------------------------

    def recuperar(self) -> int:
        """Al arranque: vuelca la auditoría pendiente y repara un bloqueo perdido.

        Si el proceso murió con la base caída, el evento quedó en el archivo y
        la fila sigue diciendo «inactivo». Sin este paso, un reinicio abriría
        el sistema en silencio, que es exactamente lo que RF-503 prohíbe.

        Devuelve cuántos eventos pendientes entraron en la base.
        """
        registros = self._cola.leer()
        disparos = [r for r in registros if _es_disparo(r)]
        volcadas = self._cola.volcar_pendientes(self._auditor)
        if not disparos:
            return volcadas

        try:
            estado = self._repo.leer()
        except Exception as exc:  # noqa: BLE001
            logger.error("No se pudo leer el estado para recuperar el bloqueo: %s", exc)
            return volcadas
        if estado is None or estado.activo:
            return volcadas  # ya está bien, o no hay fila que comparar

        ultimo = disparos[-1].get("evento") or {}
        payload = ultimo.get("payload") or {}
        try:
            reparado = self._repo.activar(
                motivo=str(payload.get("motivo") or "Bloqueo recuperado tras un reinicio"),
                origen=str(payload.get("origen") or "AUTOMATICO"),
                actor=str(ultimo.get("actor") or "SISTEMA"),
                ahora_ms=int(ultimo.get("timestamp_ms") or self._ahora()),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("No se pudo reparar el bloqueo: %s", exc)
            return volcadas
        if reparado:
            logger.warning(
                "Bloqueo del Kill-Switch reparado desde la auditoría pendiente."
            )
        return volcadas


def _es_disparo(registro: Any) -> bool:
    """¿Este registro pendiente describe un disparo del Kill-Switch?"""
    if not isinstance(registro, dict):
        return False
    evento = registro.get("evento")
    return isinstance(evento, dict) and evento.get("action") == ACCION_DISPARO
