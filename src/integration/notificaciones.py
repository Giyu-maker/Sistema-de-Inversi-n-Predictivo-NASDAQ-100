"""Notificaciones locales — SRS §5.4 (alertas SMS y de correo).

Dos implementaciones y una regla que manda sobre todo lo demás:

* **SMTP real** (`smtplib`): host, puerto, credenciales y destinatarios
  salen **del entorno** (`SMTP_*` en `.env`), nunca del repositorio. Sin
  configurar, el notificador es un nulo silencioso: no se inventa un
  servidor y no se queja en cada evento.
* **SMS simulado**: no hay módem ni proveedor (brecha declarada en
  CR-007), así que escribe en una bandeja de salida local **rotulado
  `simulado: true`**. Es una bandeja, no un envío: nadie recibe nada.

La regla crítica
----------------
**Una notificación fallida nunca bloquea ni retrasa el Kill-Switch.**
Por eso este módulo no se llama desde el camino de bloqueo: lo llama
`KillSwitchEngine` en el paso (e), *después* de la bandera, del
compare-and-set, de la liquidación y de la auditoría, y dentro de un hilo
aparte. Aquí solo se expone `enviar()`, que **propaga** su fallo: quién
llama decide qué hacer con él, y en el caso del Kill-Switch la decisión
está tomada —se registra y se sigue—, no se traga en silencio.

`notificar_mejor_esfuerzo()` es esa decisión, escrita una sola vez para
que el motor del Kill-Switch y la recuperación de huecos no la repitan.
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
import ssl
from dataclasses import dataclass
from datetime import date
from email.message import EmailMessage
from pathlib import Path
from typing import Protocol

from src.security.auditoria import (
    AuditoriaPostgres,
    EventoAuditoria,
    RepositorioAuditoria,
    auditar_sincrono,
)
from src.security.auditoria_pendiente import ColaAuditoriaPendiente

logger = logging.getLogger(__name__)

#: `action` de los eventos de notificación en `audit_logs`.
ACCION_ENVIADA = "NOTIFICACION_ENVIADA"
ACCION_FALLIDA = "NOTIFICACION_FALLIDA"

#: Asuntos por tipo de aviso (sin lenguaje de compra/venta).
ASUNTOS: dict[str, str] = {
    "kill-switch": "Kill-Switch activado — RiTech NASDAQ-100",
    "brecha-datos": "Brecha de datos sin recuperar — RiTech NASDAQ-100",
}


@dataclass(frozen=True)
class Aviso:
    """Un mensaje a enviar. `origen` es el tipo, no el texto."""

    origen: str  #: 'kill-switch' | 'brecha-datos'
    asunto: str
    cuerpo: str


class Notificador(Protocol):
    """Superficie común. `enviar` **propaga** el fallo: no lo traga."""

    def enviar(self, aviso: Aviso) -> None: ...


class NotificadorNulo:
    """No hace nada. Es lo que hay cuando no hay nada configurado."""

    def enviar(self, aviso: Aviso) -> None:
        del aviso


class NotificadorRoto:
    """Para pruebas: simula un canal caído desde el primer envío."""

    def __init__(self, motivo: str = "canal caído") -> None:
        self._motivo = motivo
        self.intentos: list[Aviso] = []

    def enviar(self, aviso: Aviso) -> None:
        self.intentos.append(aviso)
        raise RuntimeError(self._motivo)


class SmtpNotificador:
    """Correo real por SMTP, con TLS según el puerto.

    Las credenciales llegan como parámetros (desde el entorno) y no se
    registran en ningún log: `smtplib` ya no las imprime, y este módulo
    tampoco las escribe.
    """

    def __init__(
        self,
        *,
        host: str,
        puerto: int,
        de: str,
        para: list[str],
        usuario: str | None = None,
        clave: str | None = None,
        ssl_primero: bool = False,
        timeout_s: float = 5.0,
    ) -> None:
        if not host:
            raise ValueError("SMTP_HOST es obligatorio para enviar correo")
        if not para:
            raise ValueError("SMTP_PARA debe contener al menos un destinatario")
        self.host = host
        self.puerto = puerto
        self.de = de
        self.para = para
        self.usuario = usuario
        self.clave = clave
        self.ssl_primero = ssl_primero
        self.timeout_s = timeout_s

    def enviar(self, aviso: Aviso) -> None:
        mensaje = EmailMessage()
        mensaje["From"] = self.de
        mensaje["To"] = ", ".join(self.para)
        mensaje["Subject"] = aviso.asunto
        mensaje.set_content(aviso.cuerpo)

        if self.ssl_primero:
            with smtplib.SMTP_SSL(self.host, self.puerto, timeout=self.timeout_s) as servidor:
                self._autenticar(servidor)
                servidor.send_message(mensaje)
            return

        with smtplib.SMTP(self.host, self.puerto, timeout=self.timeout_s) as servidor:
            # STARTTLS solo donde el servidor lo ofrece (587 lo hace); en un
            # relay local como Mailpit no hay TLS que negociar.
            if servidor.has_extn("starttls"):
                servidor.starttls(context=ssl.create_default_context())
            self._autenticar(servidor)
            servidor.send_message(mensaje)

    def _autenticar(self, servidor: smtplib.SMTP) -> None:
        if self.usuario:
            servidor.login(self.usuario, self.clave or "")


class SmsSimuladoNotificador:
    """Bandeja de salida local en JSONL. **No envía nada a ningún lado.**

    Cada línea lleva `"simulado": true` y el motivo por el que es simulado,
    para que quien lea la bandeja no pueda confundirla con un envío real
    (brecha declarada en CR-007: no hay módem ni proveedor).
    """

    MOTIVO_SIMULADO = (
        "Simulado: sin módem GSM ni proveedor SMS configurado "
        "(Ítem 11 parcial, CR-007)."
    )

    def __init__(self, ruta: Path | str) -> None:
        self.ruta = Path(ruta)

    def enviar(self, aviso: Aviso) -> None:
        registro = {
            "simulado": True,
            "motivo_simulado": self.MOTIVO_SIMULADO,
            "origen": aviso.origen,
            "asunto": aviso.asunto,
            "cuerpo": aviso.cuerpo,
            "registrado_en": _ahora_ms(),
        }
        self.ruta.parent.mkdir(parents=True, exist_ok=True)
        with self.ruta.open("a", encoding="utf-8") as archivo:
            archivo.write(json.dumps(registro, ensure_ascii=False, default=str) + "\n")


class CompuestoNotificador:
    """Envía por todos los canales configurados.

    Si uno falla, los demás **siguen**: que el correo esté caído no puede
    costarle el SMS simulado (ni al revés). El primer fallo se propaga
    después de haber intentado todo, para que el llamador lo registre.
    """

    def __init__(self, canales: list[Notificador]) -> None:
        self.canales = canales

    def enviar(self, aviso: Aviso) -> None:
        fallos: list[Exception] = []
        for canal in self.canales:
            try:
                canal.enviar(aviso)
            except Exception as exc:  # noqa: BLE001 — un canal no frena al resto
                logger.error("Fallo notificando por %s: %s", type(canal).__name__, exc)
                fallos.append(exc)
        if fallos:
            raise fallos[0]


class AvisadorBrechas:
    """Aviso de «brecha de datos sin recuperar», **como máximo una vez por sesión**.

    El scheduler corre cada 5 minutos: avisar en cada tick con la fuente
    caída sería un correo por tick. La marca es la fecha de sesión (ET), y
    se pone **antes** de enviar: un envío fallido no debe reintentarse en
    bucle durante toda la sesión, porque el fallo ya quedó auditado.
    """

    def __init__(
        self,
        notificador: Notificador | None,
        auditor: RepositorioAuditoria,
        *,
        cola: ColaAuditoriaPendiente | None = None,
        ip_address: str = "127.0.0.1",
    ) -> None:
        self._notificador = notificador
        self._auditor = auditor
        self._cola = cola
        self._ip_address = ip_address
        self.avisos_enviados: list[date] = []

    def avisar(self, *, fecha: date, motivo: str, detalle: str) -> bool:
        """False si ya se avisó hoy o si no hay canal configurado."""
        if self._notificador is None:
            return False
        if fecha in self.avisos_enviados:
            return False
        self.avisos_enviados.append(fecha)

        cuerpo = (
            "El sistema detectó una brecha de datos que no se pudo recuperar.\n\n"
            f"Fecha de sesión: {fecha.strftime('%d/%m/%Y')}\n"
            f"Motivo: {motivo}\n"
            f"Detalle: {detalle or 'sin detalle adicional'}\n\n"
            "El sistema sigue operando con los datos disponibles; la serie "
            "se completará sola en cuanto la fuente vuelva a responder."
        )
        return notificar_mejor_esfuerzo(
            self._notificador,
            self._auditor,
            aviso=Aviso(origen="brecha-datos", asunto=ASUNTOS["brecha-datos"], cuerpo=cuerpo),
            actor="SISTEMA",
            ip_address=self._ip_address,
            cola=self._cola,
            contexto={"motivo_brecha": motivo, "fecha_sesion": fecha.isoformat()},
        )


def _ahora_ms() -> int:
    import time

    return int(time.time() * 1000)


def _lista(valor: str | None) -> list[str]:
    """`a@b.com, c@d.com` → lista limpia."""
    if not valor:
        return []
    return [p.strip() for p in valor.split(",") if p.strip()]


def fabricar_desde_entorno() -> Notificador:
    """Arma el notificador con lo que haya en el entorno (§15.1).

    * `SMTP_HABILITADO` en verdadero + `SMTP_HOST` → canal de correo real.
    * `SMS_SIMULADO_HABILITADO` en verdadero → bandeja local.
    * Nada configurado → `NotificadorNulo`: no se envía y no se queja.
    * Los dos → `CompuestoNotificador` (uno no frena al otro).
    """
    canales: list[Notificador] = []

    if _verdadero(os.environ.get("SMTP_HABILITADO")):
        host = os.environ.get("SMTP_HOST", "").strip()
        if host:
            try:
                canales.append(
                    SmtpNotificador(
                        host=host,
                        puerto=int(os.environ.get("SMTP_PUERTO", "587")),
                        de=os.environ.get("SMTP_DE", "").strip() or "ritech@localhost",
                        para=_lista(os.environ.get("SMTP_PARA")),
                        usuario=os.environ.get("SMTP_USUARIO") or None,
                        clave=os.environ.get("SMTP_CLAVE") or None,
                        ssl_primero=_verdadero(os.environ.get("SMTP_SSL")),
                        timeout_s=float(os.environ.get("SMTP_TIMEOUT_S", "5")),
                    )
                )
            except (ValueError, TypeError) as exc:
                logger.error("SMTP mal configurado, canal omitido: %s", exc)
        else:
            logger.error("SMTP_HABILITADO sin SMTP_HOST: canal de correo omitido.")

    if _verdadero(os.environ.get("SMS_SIMULADO_HABILITADO")):
        ruta = os.environ.get("SMS_RUTA_SALIDA", "").strip() or "logs/sms-salida.jsonl"
        canales.append(SmsSimuladoNotificador(ruta))

    if not canales:
        logger.info(
            "Notificaciones deshabilitadas: sin SMTP_HABILITADO ni "
            "SMS_SIMULADO_HABILITADO en el entorno."
        )
        return NotificadorNulo()
    if len(canales) == 1:
        return canales[0]
    return CompuestoNotificador(canales)


def _verdadero(valor: str | None) -> bool:
    return (valor or "").strip().lower() in ("1", "true", "si", "sí", "yes", "on")


def notificar_mejor_esfuerzo(
    notificador: Notificador | None,
    auditor: RepositorioAuditoria,
    *,
    aviso: Aviso,
    actor: str,
    ip_address: str,
    cola: ColaAuditoriaPendiente | None = None,
    contexto: dict | None = None,
) -> bool:
    """Envía y registra el resultado en `audit_logs`. **Nunca lanza.**

    Es la implementación de «best-effort» que pide la regla crítica: el
    envío se intenta, el éxito o el fallo queda auditado
    (`NOTIFICACION_ENVIADA` / `NOTIFICACION_FALLIDA`), y si ni siquiera el
    registro cabe, se va a la cola pendiente. Devuelve `True` si el envío
    salió bien.
    """
    if notificador is None:
        return False

    enviado = False
    fallo: str | None = None
    try:
        notificador.enviar(aviso)
        enviado = True
    except Exception as exc:  # noqa: BLE001 — best-effort por definición
        fallo = str(exc)
        logger.error("No se pudo enviar la notificación (%s): %s", aviso.origen, exc)

    payload = dict(contexto or {})
    payload.update(
        {
            "origen_aviso": aviso.origen,
            "asunto": aviso.asunto,
            "enviado": enviado,
        }
    )
    if fallo:
        payload["fallo"] = fallo

    evento = EventoAuditoria(
        action=ACCION_ENVIADA if enviado else ACCION_FALLIDA,
        actor=actor,
        ip_address=ip_address,
        payload=payload,
        timestamp_ms=_ahora_ms(),
    )
    try:
        auditar_sincrono(auditor, evento)
    except Exception as exc:  # noqa: BLE001 — ni el registro puede frenar nada
        logger.error("No se pudo registrar el resultado de la notificación: %s", exc)
        if cola is not None:
            cola.registrar(evento, str(exc))
    return enviado


def avisador_desde_entorno() -> AvisadorBrechas:
    """Avisador de brechas con el canal y la auditoría del entorno.

    El scheduler es quien conoce las brechas, y este es su punto de entrada:
    si no hay nada configurado devuelve igual un avisador (con un
    notificador nulo), para que el ciclo no tenga que tratar `None` en dos
    sitios distintos.
    """
    ruta = os.environ.get("AUDITORIA_PENDIENTE_RUTA", "").strip()
    cola = ColaAuditoriaPendiente(ruta) if ruta else None
    return AvisadorBrechas(
        fabricar_desde_entorno(),
        AuditoriaPostgres(),
        cola=cola,
    )
