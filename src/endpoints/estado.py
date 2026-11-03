"""`GET /api/status` — el latido que el dashboard sondea cada 10 s (D8).

La regla de estado (provisional, documentada en `docs/despliegue.md`)
---------------------------------------------------
* **Fuera de sesión** → `CERRADO`, neutro: «Mercado cerrado». Sin ese matiz, la
  antigüedad del dato dejaría el estado en amarillo 17 horas cada día: por la
  noche no llegan ticks porque no los hay.
* **Dentro de sesión** (09:30–16:00 ET, lunes a viernes, feriados NYSE
  configurados) → `VERDE` o `AMARILLO` según estas causas:

  1. dato desactualizado: más de 2 intervalos de vela (10 min) sin ticks;
  2. la IA de M1 no responde, o responde con el modelo sin cargar;
  3. la última predicción existe y no es `STABLE` (RF-403 / AGENTS.md §8);
  4. PostgreSQL no responde (añadido al plan: sin la tabla no se puede saber
     qué pasa con la última predicción, y pintar verde sería mentir).

**No** es causa de amarillo la ventana incompleta de la sesión (las primeras 12
velas del día): es el sistema esperando datos que todavía no existen, no una
degradación (decisión D6).

No hay estado rojo salvo el Kill-Switch (RF-501): el rojo es exclusivo del
bloqueo de emergencia, y manda incluso con el mercado cerrado. El resto de
las causas siguen pintando amarillo.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from src.database.predictions import PrediccionGuardada
from src.endpoints.convertidores import (
    a_prediccion_salida,
    a_resumen_salida,
    a_senal_salida,
)
from src.endpoints.dependencias import (
    EstadoAplicacion,
    configuracion_activa,
    consultar_ia,
    estado_app,
    senal_de,
)
from src.endpoints.esquemas import (
    EstadoSistemaSalida,
    IaSalida,
    KillSwitchSalida,
    MercadoSalida,
    ModoSalida,
    PerfilSalida,
    SenalSalida,
)
from src.ingestion.engine import NasdaqCandle
from src.security.kill_switch import ResumenKillSwitch
from src.security.perfiles import senal_sin_prediccion
from src.utils.mercado import mercado

router = APIRouter()

DESCRIPCION_ASESOR = (
    "La IA solo recomienda; un operador humano ejecuta cada orden manualmente."
)
MOTIVO_BLOQUEO = (
    "Requiere autorización explícita de la Gerente General y autenticación "
    "multifactor local (OTP)."
)

#: Resumen usado cuando el proceso ni siquiera tiene motor configurado: no es
#: «desactivado», es «no verificado», y por eso bloquea las órdenes.
SIN_MOTOR = ResumenKillSwitch(
    activo=None,
    motivo=None,
    origen=None,
    liquidacion_estado="NINGUNA",
    liquidaciones=0,
    disparado_en=None,
    reinicio_en=None,
    detalle=(
        "El servicio de Kill-Switch no está configurado en este proceso: "
        "no se emitirán órdenes ni sugerencias."
    ),
)


def resumen_kill_switch(estado: EstadoAplicacion) -> ResumenKillSwitch:
    """Estado del bloqueo; `SIN_MOTOR` si la app no lo tiene montado."""
    motor = estado.motor_kill_switch
    return SIN_MOTOR if motor is None else motor.resumen()


@router.get("/api/status", response_model=EstadoSistemaSalida, tags=["estado"])
def status(estado: EstadoAplicacion = Depends(estado_app)) -> EstadoSistemaSalida:
    """Estado del sistema, mercado, IA, modo, perfil y última predicción."""
    ahora = estado.ahora()
    ahora_ms = int(ahora.timestamp() * 1000)
    est_mercado = mercado(ahora, estado.feriados)
    vigente = configuracion_activa(estado)
    configuracion = vigente.configuracion
    avisos = list(vigente.avisos)

    ultima_vela, vela_error = _ultima_vela(estado)
    ultima_pred, error_pg = _ultima_prediccion(estado, vigente.postgres_ok)
    if not vigente.postgres_ok and error_pg is None:
        # La caída ya la detectó `configuracion_activa` al leer la configuración:
        # aquí se recicla ese mensaje como CAUSA del estado, no solo como aviso.
        # Sin la tabla no se puede saber si la última predicción es STABLE, y
        # responder VERDE sería mentir.
        error_pg = next(
            (aviso for aviso in vigente.avisos if "base de datos no responde" in aviso),
            "La base de datos no responde: no se pudo leer la última predicción.",
        )
    ia = consultar_ia(estado)
    bloqueo = resumen_kill_switch(estado)

    nombre_estado, motivo = _regla(
        abierto=est_mercado.abierto,
        motivo_mercado=est_mercado.motivo,
        ultima_vela_ms=ultima_vela.timestamp if ultima_vela else None,
        ahora_ms=ahora_ms,
        retraso_max_s=estado.retraso_max_s,
        vela_error=vela_error,
        ia=ia,
        ultima_prediccion=ultima_pred,
        error_pg=error_pg,
        bloqueo=bloqueo.activo,
        motivo_bloqueo=bloqueo.detalle,
    )

    # Avisos: lo que conviene saber pero no decide el estado (o que ya está en
    # el motivo y no debe repetirse a pantalla completa).
    if bloqueo.activo is None:
        # Estado no verificado: no pinta ROJO (eso es «activo») pero tampoco
        # puede pasar desapercibido.
        avisos.append(bloqueo.detalle)
    if est_mercado.abierto:
        if error_pg:
            avisos.append(error_pg)
        if vela_error:
            avisos.append(vela_error)
        if (
            ultima_vela is not None
            and ultima_pred is None
            and error_pg is None
            and vela_error is None
        ):
            avisos.append(
                "Sin predicción registrada todavía: la ventana de 12 velas de "
                "la sesión aún no está completa (no altera el estado)."
            )
    else:
        avisos.extend([texto for texto in (vela_error, error_pg) if texto])

    # Señal: con el bloqueo activo (o sin poder verificarlo) la respuesta es
    # «no operable» con el texto del backend, sea cual sea la predicción.
    senal_base = (
        senal_de(ultima_pred, configuracion.risk_profile, avisos)
        if ultima_pred is not None
        else senal_sin_prediccion(configuracion.risk_profile)
    )
    if bloqueo.activo is False:
        senal_salida = a_senal_salida(senal_base)
    else:
        senal_salida = SenalSalida(
            operable=False,
            motivo=bloqueo.detalle,
            umbral=senal_base.umbral,
            perfil=senal_base.perfil,
            tendencia=senal_base.tendencia,
            confianza=senal_base.confianza,
        )

    return EstadoSistemaSalida(
        estado=nombre_estado,
        motivo=motivo,
        mercado=MercadoSalida(
            abierto=est_mercado.abierto,
            motivo=est_mercado.motivo,
            feriado=est_mercado.feriado,
            zona=est_mercado.zona,
            horario=est_mercado.horario,
        ),
        ultima_vela=a_resumen_salida(ultima_vela, ahora_ms=ahora_ms),
        retraso_max_s=estado.retraso_max_s,
        ia=ia,
        modo=ModoSalida(
            nombre="AUTONOMO" if configuracion.is_autonomous else "ASESOR",
            descripcion=DESCRIPCION_ASESOR,
            # La ejecución automática no existe en esta fase: ni siquiera con
            # la fila de configuración en TRUE. Se informa arriba si está.
            autonomo_disponible=False,
            motivo_bloqueo=MOTIVO_BLOQUEO,
        ),
        perfil=PerfilSalida(
            nombre=configuracion.risk_profile,
            umbral_confianza=configuracion.umbral_confianza,
            max_loss_day=configuracion.max_loss_day,
            por_defecto=vigente.por_defecto,
        ),
        ultima_prediccion=(
            a_prediccion_salida(ultima_pred) if ultima_pred is not None else None
        ),
        senal=senal_salida,
        kill_switch=KillSwitchSalida(
            activo=bloqueo.activo,
            detalle=bloqueo.detalle,
            motivo=bloqueo.motivo,
            origen=bloqueo.origen,
            liquidacion_estado=bloqueo.liquidacion_estado,
            liquidaciones=bloqueo.liquidaciones,
            disparado_en=bloqueo.disparado_en,
            reinicio_en=bloqueo.reinicio_en,
        ),
        avisos=avisos,
    )


def _ultima_vela(estado: EstadoAplicacion) -> tuple[NasdaqCandle | None, str | None]:
    """Vela más reciente (o el por qué no se pudo leer)."""
    try:
        vela = estado.lector.ultima_vela()
    except Exception as exc:  # noqa: BLE001 — InfluxDB caído no puede tumbar el estado
        return None, f"No se pudo leer la base de precios: {exc}"
    return vela, None


def _ultima_prediccion(
    estado: EstadoAplicacion, postgres_ok: bool
) -> tuple[PrediccionGuardada | None, str | None]:
    if not postgres_ok:
        return None, None
    try:
        return estado.predicciones.ultima(), None
    except Exception as exc:  # noqa: BLE001 — idem para PostgreSQL
        return None, f"No se pudo leer la base de datos: {exc}"


def _regla(
    *,
    abierto: bool,
    motivo_mercado: str,
    ultima_vela_ms: int | None,
    ahora_ms: int,
    retraso_max_s: int,
    vela_error: str | None,
    ia: IaSalida,
    ultima_prediccion: PrediccionGuardada | None,
    error_pg: str | None,
    bloqueo: bool | None,
    motivo_bloqueo: str,
) -> tuple[str, str]:
    """La regla pura, separada del I/O para poder probarla sin endpoint.

    El orden es el criterio: **primero** el Kill-Switch —un estado de
    emergencia se ve siempre, hasta con el mercado cerrado—, después el
    mercado, y al final el resto de las causas.
    """
    if bloqueo is True:
        return "ROJO", motivo_bloqueo
    if not abierto:
        # P2.2: el chip ya dice «Mercado cerrado» — el motivo aporta el qué
        # y cuándo se reabre (frase CALCULADA en `mercado.motivo`).
        return "CERRADO", f"{motivo_mercado}."

    causas: list[str] = []  # problemas del sistema → «Revisar el sistema» (P1.7)
    inestable = False       # tendencia ≠ STABLE → «Mercado inestable» (P1.7)
    if bloqueo is None:
        # Estado del Kill-Switch no verificado: no es «activado», pero el
        # sistema no puede afirmar que está operando con normalidad.
        causas.append("no se pudo verificar el estado del Kill-Switch")
    if vela_error:
        causas.append(vela_error)
    elif ultima_vela_ms is None:
        causas.append("sin velas registradas en horario de mercado")
    else:
        antiguedad_s = max(0, (ahora_ms - ultima_vela_ms) // 1000)
        if antiguedad_s > retraso_max_s:
            causas.append(
                f"datos desactualizados (la última vela tiene "
                f"{antiguedad_s // 60} min de antigüedad, máximo "
                f"{retraso_max_s // 60} min)"
            )
    if not ia.disponible:
        causas.append(ia.detalle)
    if ultima_prediccion is not None and ultima_prediccion.trend_prediction != "STABLE":
        # P1.7: la tendencia NO se pinta aquí (los colores y textos de estado
        # no comunican dirección de mercado, solo si hay movimiento esperado).
        inestable = True
    if error_pg:
        causas.append(error_pg)

    partes: list[str] = []
    if inestable:
        partes.append(
            "Mercado inestable: la IA espera movimiento en los próximos 15 minutos"
        )
    if causas:
        partes.append("Revisar el sistema: " + " / ".join(causas))
    if partes:
        return "AMARILLO", ". ".join(partes)
    return "VERDE", "Dato actualizado, IA disponible y última predicción sin movimiento."
