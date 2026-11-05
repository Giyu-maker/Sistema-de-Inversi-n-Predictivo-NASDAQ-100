"""`/api/kill-switch` — estado, disparo y reinicio (RF-501/502/503, CR-006).

Tres rutas y tres niveles de exigencia, que es justo lo que pide el SRS:

* **`GET /api/kill-switch`** — solo lectura, como el resto de la API.
* **`POST /api/kill-switch`** — **sin clave** (RF-501). En una emergencia no
  se pide credencial a quien intenta parar el sistema: el botón tiene que
  funcionar para cualquiera que pueda tocar la pantalla, y quién lo pulsó
  queda en la auditoría como `OPERADOR` con su IP.
* **`POST /api/kill-switch/reset`** — **con clave de reinicio** (CR-006).
  Volver a dejar operable un sistema que se detuvo a sí mismo sí exige
  autorización: hash en el entorno, comparación en tiempo constante, límite
  de intentos persistido en `kill_switch_state`, y actor `CLAVE_REINICIO`
  (sin identidad personal, porque no existe autenticación de usuarios).

El reinicio es el ejemplo de **lectura estricta** de SDD §7 regla 3: si el
log de auditoría no se inserta, no se reinicia. Lo contrario —el disparo— es
deliberadamente tolerante a fallos, y esa asimetría está justificada en
`src/security/kill_switch.py`.

Límite de intentos
------------------
`KILL_SWITCH_MAX_ATTEMPTS` (5 por defecto) dentro de una ventana de
`KILL_SWITCH_VENTANA_INTENTOS_S` (900 s). El contador vive en la base, así
que sobrevive a un reinicio del proceso, y se limpia al llegar al límite o
al reiniciar con éxito.
"""

from __future__ import annotations

import os
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from src.endpoints.dependencias import EstadoAplicacion, estado_app
from src.endpoints.esquemas import DisparoSalida, KillSwitchSalida, ReinicioSalida
from src.security import clave_reinicio
from src.security.kill_switch import KillSwitchEngine, ResumenKillSwitch
from src.utils.mercado import ahora_ny

router = APIRouter()

MOTIVO_PULSACION = "Pulsación manual desde el panel."

#: Clave en la cabecera: no viaja en el cuerpo, que es lo que más suele
#: quedar registrado en los logs de las pasarelas.
CABECERA_CLAVE = "X-Kill-Switch-Key"


def _motor(estado: EstadoAplicacion) -> KillSwitchEngine:
    """Motor del Kill-Switch; 503 si la app no lo tiene configurado."""
    motor = estado.motor_kill_switch
    if motor is None:
        raise HTTPException(
            status_code=503,
            detail="El servicio de Kill-Switch no está configurado en este proceso.",
        )
    return motor


def _ip(request: Request) -> str:
    """IP del equipo en la intranet (AGENTS.md §5.3)."""
    cliente = request.client
    return cliente.host if cliente and cliente.host else "127.0.0.1"


def _a_salida(resumen: ResumenKillSwitch) -> KillSwitchSalida:
    return KillSwitchSalida(
        activo=resumen.activo,
        detalle=resumen.detalle,
        motivo=resumen.motivo,
        origen=resumen.origen,
        liquidacion_estado=resumen.liquidacion_estado,
        liquidaciones=resumen.liquidaciones,
        disparado_en=resumen.disparado_en,
        reinicio_en=resumen.reinicio_en,
    )


def _limites() -> tuple[int, int]:
    """(máximo de intentos, ventana en segundos) leídos del entorno."""
    maximos = int(os.environ.get("KILL_SWITCH_MAX_ATTEMPTS", "5"))
    ventana = int(os.environ.get("KILL_SWITCH_VENTANA_INTENTOS_S", "900"))
    return max(1, maximos), max(1, ventana)


@router.get("/api/kill-switch", response_model=KillSwitchSalida, tags=["kill-switch"])
def estado_kill_switch(estado: EstadoAplicacion = Depends(estado_app)) -> KillSwitchSalida:
    """Estado actual del bloqueo, con el texto llano que pinta el panel."""
    return _a_salida(_motor(estado).resumen())


@router.post("/api/kill-switch", response_model=DisparoSalida, tags=["kill-switch"])
def disparar(request: Request, estado: EstadoAplicacion = Depends(estado_app)) -> DisparoSalida:
    """Dispara el Kill-Switch (RF-501). **Sin clave**: la emergencia no espera.

    El orden de ejecución —bandera, base, liquidación, auditoría— es el de
    `KillSwitchEngine.execute_global_liquidation`; aquí solo se orquesta.
    """
    motor = _motor(estado)
    resultado = motor.execute_global_liquidation(
        motivo=MOTIVO_PULSACION,
        origen="MANUAL",
        actor="OPERADOR",
        ip_address=_ip(request),
    )
    resumen = motor.resumen()
    return DisparoSalida(
        activo=resumen.activo,
        # El texto del disparo es el que importa aquí: dice qué se hizo (o que
        # ya estaba hecho) con sus métricas.
        detalle=resultado.detalle,
        motivo=resumen.motivo,
        origen=resumen.origen,
        liquidacion_estado=resumen.liquidacion_estado,
        liquidaciones=resumen.liquidaciones,
        disparado_en=resumen.disparado_en,
        reinicio_en=resumen.reinicio_en,
        primera_vez=resultado.primera_vez,
        ms_bloqueo=round(resultado.ms_bloqueo, 3),
        ms_persistencia=round(resultado.ms_persistencia, 3),
        ms_liquidacion=round(resultado.ms_liquidacion, 3),
        ordenes_simuladas=resultado.ordenes,
        persistencia_ok=resultado.persistencia_ok,
        auditoria_ok=resultado.auditoria_ok,
    )


@router.post("/api/kill-switch/reset", response_model=ReinicioSalida, tags=["kill-switch"])
def reiniciar(
    request: Request,
    clave: str | None = Header(default=None, alias=CABECERA_CLAVE),
    estado: EstadoAplicacion = Depends(estado_app),
) -> ReinicioSalida:
    """Restablece el sistema tras un disparo (RF-503). Exige la clave.

    Cada fallo tiene su código y su frase en español: 401 (clave incorrecta),
    429 (límite de intentos), 503 (no configurado o base caída).
    """
    motor = _motor(estado)
    hash_guardado = clave_reinicio.desde_entorno()
    if hash_guardado is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "El reinicio no está configurado: falta "
                f"{clave_reinicio.VARIABLE} en el entorno. "
                "Contacte al administrador de TI."
            ),
        )

    actual = motor.estado()
    if actual is None:
        raise HTTPException(
            status_code=503,
            detail="No se pudo leer el estado del Kill-Switch. Intente de nuevo.",
        )
    if not actual.activo:
        return ReinicioSalida(
            reiniciado=False,
            detalle="El Kill-Switch ya estaba desactivado.",
            intentos_reinicio=actual.intentos_reinicio,
        )

    maximos, ventana_s = _limites()
    ahora_ms = int(time.time() * 1000)
    dentro_de_ventana = (
        actual.ultimo_intento_en > 0
        and (ahora_ms - actual.ultimo_intento_en) <= ventana_s * 1000
    )
    if dentro_de_ventana and actual.intentos_reinicio >= maximos:
        restantes_s = ventana_s - (ahora_ms - actual.ultimo_intento_en) // 1000
        minutos = max(1, (restantes_s + 59) // 60)
        raise HTTPException(
            status_code=429,
            detail=(
                f"Demasiados intentos de reinicio ({actual.intentos_reinicio}). "
                f"Vuelva a intentarlo en {minutos} minuto(s)."
            ),
        )

    if not clave_reinicio.verificar(clave or "", hash_guardado):
        intentos = motor.registrar_intento_fallido(ahora_ms)
        raise HTTPException(
            status_code=401,
            detail=(
                "Clave de reinicio incorrecta."
                + (
                    f" Intentos en esta ventana: {intentos} de {maximos}."
                    if intentos is not None
                    else ""
                )
            ),
        )

    # Auditoría estricta ANTES de la acción: sin su registro no hay reinicio
    # (SDD §7 regla 3). Si el INSERT falla, `ErrorDeAuditoria` sube y lo
    # traduce `errores.py` a un 503 que dice «No se ejecutó»: la excepción
    # corta el flujo antes de tocar el estado, y el sistema sigue bloqueado.
    hecho = motor.reiniciar(
        actor="CLAVE_REINICIO",
        ip_address=_ip(request),
        ahora_ms=ahora_ms,
        # Fecha de la sesión en ET: es la que guarda la guarda anti-bucle
        # del disparo automático (la calcula `src/utils/mercado.py`).
        sesion=ahora_ny().date(),
    )

    if not hecho:
        return ReinicioSalida(
            reiniciado=False,
            detalle="El Kill-Switch ya estaba desactivado.",
            intentos_reinicio=0,
        )
    return ReinicioSalida(
        reiniciado=True,
        detalle=(
            "Reinicio registrado. El sistema vuelve a operar: se reanudarán las "
            "señales y las órdenes simuladas según el modo vigente."
        ),
        intentos_reinicio=0,
    )
