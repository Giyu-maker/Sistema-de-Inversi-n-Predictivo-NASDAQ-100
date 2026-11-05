"""`GET /api/reportes/{tipo}` — descarga de reportes tabulares (RF-702).

Cuatro tipos, dos formatos, una cabecera idéntica en ambos:

| tipo | contenido | de dónde sale |
|---|---|---|
| `senal` | predicciones por sesión con umbral, operabilidad y resultado real | `ai_predictions` + velas (InfluxDB) |
| `rendimiento` | apertura, cierre, variación y rendimiento 2x por sesión (RF-201/RF-203) | velas (InfluxDB) |
| `kill-switch` | eventos de activación y reinicio | `audit_logs` |
| `auditoria` | conteos por acción y actor | `audit_logs` |

Decisiones que están en el propio endpoint y no en la UI:

* **Rango**: `desde`/`hasta` como `AAAA-MM-DD` de **sesión** (ET, resuelto
  con `src/utils/mercado.py`). Sin parámetros se sirven las **últimas 5
  sesiones**. Fecha mal formada o `desde > hasta` → 422 con frase en
  español; **un rango sin datos devuelve el archivo con su cabecera y cero
  filas**, porque «no hay datos en ese periodo» es un resultado, no un
  error.
* **Solo lectura**: el endpoint no escribe en ninguna base; los reportes
  se generan en memoria y se devuelven como descarga.
"""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from src.ai.features import session_of
from src.ai.prediccion import a_candle
from src.calculator.asymmetric import AsymmetricCalculator
from src.database.auditoria import FiltroAuditoria, LectorAuditoria
from src.endpoints.dependencias import (
    EstadoAplicacion,
    configuracion_activa,
    consultar_calidad,
    estado_app,
)
from src.endpoints.predicciones import resultados_en_lote
from src.security.perfiles import evaluar_senal
from src.utils.fechas import ZONA_MERCADO, fecha_hora
from src.utils.mercado import apertura_ms
from src.utils.reportes import (
    FORMATOS_REPORTE,
    LIMITE_FILAS,
    TIPOS_REPORTE,
    TITULOS,
    EncabezadoReporte,
    a_csv,
    a_pdf,
    fecha_colombia,
    fecha_de_epoch_ny,
    fecha_ny,
    hora_ny,
    metrica_de_modelo,
    rango_texto,
)

logger = logging.getLogger(__name__)

router = APIRouter()

#: Sesiones que sirven el rango por defecto (P3).
SESIONES_POR_DEFECTO = 5

#: Acciones que componen el reporte de eventos del Kill-Switch.
ACCIONES_KILL_SWITCH = ("KILL_SWITCH_TRIGGERED", "KILL_SWITCH_RESET")


@router.get("/api/reportes/{tipo}", tags=["reportes"])
def reporte(
    tipo: str,
    desde: str | None = None,
    hasta: str | None = None,
    formato: str = "csv",
    estado: EstadoAplicacion = Depends(estado_app),
) -> Response:
    """Descarga un reporte en CSV (UTF-8 con BOM) o PDF.

    Los parámetros son opcionales; sin ellos se sirve el rango por defecto.
    """
    if tipo not in TIPOS_REPORTE:
        validos = ", ".join(TIPOS_REPORTE)
        raise HTTPException(
            status_code=422, detail=f"Tipo de reporte no válido: {tipo!r} (válidos: {validos})."
        )
    if formato not in FORMATOS_REPORTE:
        validos = ", ".join(FORMATOS_REPORTE)
        raise HTTPException(
            status_code=422,
            detail=f"Formato no válido: {formato!r} (válidos: {validos}).",
        )

    dia_desde, dia_hasta = _resolver_rango(estado, desde, hasta)
    desde_ms = apertura_ms(dia_desde)
    hasta_ms = apertura_ms(dia_hasta + timedelta(days=1)) - 1

    columnas, filas = _reunir(tipo, estado, dia_desde, dia_hasta, desde_ms, hasta_ms)

    encabezado = EncabezadoReporte(
        titulo=TITULOS[tipo],
        generado_en=fecha_hora(int(time.time() * 1000)),
        rango=rango_texto(dia_desde, dia_hasta),
        metrica=metrica_de_modelo(consultar_calidad(estado)),
        filas=len(filas),
    )

    nombre = f"reporte_{tipo}_{dia_desde.isoformat()}_{dia_hasta.isoformat()}.{formato}"
    if formato == "csv":
        cuerpo = a_csv(encabezado, columnas, filas).encode("utf-8")
        media = "text/csv; charset=utf-8"
    else:
        cuerpo = a_pdf(encabezado, columnas, filas)
        media = "application/pdf"

    return Response(
        content=cuerpo,
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{nombre}"'},
    )


# -- rango ------------------------------------------------------------------


def _fecha_o_none(texto: str | None, nombre: str) -> date | None:
    """`AAAA-MM-DD` → `date`; sin valor → None; mal formada → 422."""
    if texto is None or not texto.strip():
        return None
    try:
        return date.fromisoformat(texto.strip())
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Fecha inválida en «{nombre}»: use AAAA-MM-DD (ejemplo: 2026-11-03).",
        ) from exc


def _resolver_rango(
    estado: EstadoAplicacion, desde: str | None, hasta: str | None
) -> tuple[date, date]:
    """Rango pedido o, si no hay, las últimas `SESIONES_POR_DEFECTO` sesiones.

    Si falta un extremo, el rango es de un solo día (el que llegó): es
    predecible y evita inventar un segundo límite.
    """
    dia_desde = _fecha_o_none(desde, "desde")
    dia_hasta = _fecha_o_none(hasta, "hasta")

    if dia_desde is None and dia_hasta is None:
        return _ultimas_sesiones(estado, SESIONES_POR_DEFECTO)
    if dia_desde is None:
        dia_desde = dia_hasta
    if dia_hasta is None:
        dia_hasta = dia_desde

    if dia_desde > dia_hasta:
        raise HTTPException(
            status_code=422,
            detail=(
                "El rango no puede empezar después de terminar: "
                f"desde {dia_desde.strftime('%d/%m/%Y')} es posterior a "
                f"hasta {dia_hasta.strftime('%d/%m/%Y')}."
            ),
        )
    return dia_desde, dia_hasta


def _ultimas_sesiones(estado: EstadoAplicacion, cantidad: int) -> tuple[date, date]:
    """Primeras y últimas fechas de las últimas `cantidad` sesiones con datos.

    Sale de las velas que ya están en InfluxDB: «últimas 5 sesiones» quiere
    decir sesiones reales, no cinco días naturales. Si no hay velas (base
    vacía o caída), se cae a cinco días naturales terminando hoy en ET —
    el reporte saldrá igual con su cabecera y cero filas.
    """
    try:
        velas = estado.lector.ultimas_velas(400)
    except Exception as exc:  # noqa: BLE001 — el reporte no depende de eso
        logger.warning("Sin velas para el rango por defecto: %s", exc)
        velas = []

    if velas:
        sesiones = session_of([a_candle(v) for v in velas])
        fechas = [fecha_de_epoch_ny(s[0].timestamp) for s in sesiones[-cantidad:]]
        return min(fechas), max(fechas)

    hoy = estado.ahora().astimezone(ZONA_MERCADO).date()
    return hoy - timedelta(days=cantidad - 1), hoy


# -- datos por tipo ---------------------------------------------------------


def _reunir(
    tipo: str,
    estado: EstadoAplicacion,
    dia_desde: date,
    dia_hasta: date,
    desde_ms: int,
    hasta_ms: int,
) -> tuple[list[str], list[list[Any]]]:
    """Columnas y filas del tipo pedido; los fallos de base → 503 en español."""
    try:
        if tipo == "senal":
            return _columnas_senal(estado, desde_ms, hasta_ms)
        if tipo == "rendimiento":
            return _columnas_rendimiento(estado, dia_desde, dia_hasta, desde_ms, hasta_ms)
        if tipo == "kill-switch":
            return _columnas_kill_switch(estado, desde_ms, hasta_ms)
        return _columnas_auditoria(estado, desde_ms, hasta_ms)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — cualquier fallo de I/O
        raise HTTPException(
            status_code=503, detail=f"No se pudo generar el reporte: {exc}"
        ) from exc


def _columnas_senal(
    estado: EstadoAplicacion, desde_ms: int, hasta_ms: int
) -> tuple[list[str], list[list[Any]]]:
    predicciones, _total = estado.predicciones.listar_rango(
        desde_ms, hasta_ms, LIMITE_FILAS, 0
    )
    avisos: list[str] = []
    resultados = resultados_en_lote(predicciones, estado, avisos)
    if avisos:
        logger.warning("Reporte de señales sin resultado real: %s", " ".join(avisos))
    perfil = configuracion_activa(estado).configuracion.risk_profile

    columnas = [
        "sesion", "hora_et", "tendencia", "confianza_pct", "umbral_pct",
        "operable", "resultado", "retorno_pct", "etiqueta_real", "motivo", "xai", "id",
    ]
    filas: list[list[Any]] = []
    for p in predicciones:
        senal = evaluar_senal(p.trend_prediction, p.confidence_score, perfil)
        resultado = resultados.get(p.prediction_time)
        filas.append(
            [
                fecha_ny(p.prediction_time),
                hora_ny(p.prediction_time),
                p.trend_prediction,
                round(p.confidence_score * 100, 2),
                round(senal.umbral * 100, 2),
                senal.operable,
                resultado.estado if resultado else "",
                round(resultado.retorno_pct, 4) if resultado and resultado.retorno_pct is not None else None,
                resultado.etiqueta if resultado else None,
                senal.motivo,
                p.technical_rationale,
                p.id,
            ]
        )
    return columnas, filas


def _columnas_rendimiento(
    estado: EstadoAplicacion, dia_desde: date, dia_hasta: date, desde_ms: int, hasta_ms: int
) -> tuple[list[str], list[list[Any]]]:
    velas = estado.lector.velas_rango(desde_ms, hasta_ms)
    columnas = [
        "sesion", "apertura", "cierre", "variacion_pct", "rendimiento_ritech_pct",
        "limite_pct", "umbral_alcanzado", "gap",
    ]
    filas: list[list[Any]] = []
    for sesion in session_of([a_candle(v) for v in velas]):
        dia = fecha_de_epoch_ny(sesion[0].timestamp)
        if not (dia_desde <= dia <= dia_hasta):
            continue
        apertura = sesion[0].open
        cierre = sesion[-1].close
        if apertura <= 0:
            continue  # apertura inválida no produce un rendimiento creíble
        variacion = (cierre - apertura) / apertura * 100
        filas.append(
            [
                dia.strftime("%d/%m/%Y"),
                round(apertura, 4),
                round(cierre, 4),
                round(variacion, 4),
                round(AsymmetricCalculator.calculate_yield(variacion), 4),
                AsymmetricCalculator.MAX_LOSS_PCT,
                AsymmetricCalculator.is_threshold_breached(apertura, cierre),
                AsymmetricCalculator.is_market_gap(variacion),
            ]
        )
    filas.sort(reverse=True, key=lambda f: f[0])  # sesión más reciente primero
    return columnas, filas


def _columnas_kill_switch(
    estado: EstadoAplicacion, desde_ms: int, hasta_ms: int
) -> tuple[list[str], list[list[Any]]]:
    lector = _lector_auditoria(estado)
    registros, _total = lector.listar(
        FiltroAuditoria(
            desde_ms=desde_ms, hasta_ms=hasta_ms, acciones=ACCIONES_KILL_SWITCH,
            limit=LIMITE_FILAS, offset=0,
        )
    )
    columnas = [
        "fecha_hora", "accion", "actor", "ip", "motivo", "origen",
        "liquidacion", "ordenes", "ms_bloqueo", "ms_persistencia", "ms_liquidacion",
        "persistencia_ok", "auditoria_ok",
    ]
    filas: list[list[Any]] = []
    for r in registros:
        payload = _como_dict(r.payload)
        filas.append(
            [
                fecha_colombia(r.timestamp),
                r.action,
                r.actor,
                r.ip_address,
                payload.get("motivo"),
                payload.get("origen"),
                payload.get("liquidacion_estado"),
                payload.get("ordenes_simuladas"),
                payload.get("ms_bloqueo"),
                payload.get("ms_persistencia"),
                payload.get("ms_liquidacion"),
                payload.get("persistencia_ok"),
                payload.get("auditoria_ok"),
            ]
        )
    return columnas, filas


def _columnas_auditoria(
    estado: EstadoAplicacion, desde_ms: int, hasta_ms: int
) -> tuple[list[str], list[list[Any]]]:
    lector = _lector_auditoria(estado)
    resumen = lector.resumen(desde_ms, hasta_ms)
    columnas = ["accion", "actor", "registros", "primera", "ultima"]
    filas: list[list[Any]] = [
        [
            fila.accion,
            fila.actor,
            fila.registros,
            fecha_colombia(fila.primera),
            fecha_colombia(fila.ultima),
        ]
        for fila in resumen[:LIMITE_FILAS]
    ]
    return columnas, filas


def _lector_auditoria(estado: EstadoAplicacion) -> LectorAuditoria:
    if estado.auditoria is None:
        raise HTTPException(
            status_code=503,
            detail="El lector de auditoría no está configurado en este proceso.",
        )
    return estado.auditoria


def _como_dict(payload: str | None) -> dict[str, Any]:
    """`payload` es JSON en texto; si está roto, se trata como vacío."""
    if not payload:
        return {}
    import json

    try:
        datos = json.loads(payload)
    except (ValueError, TypeError):
        return {}
    return datos if isinstance(datos, dict) else {}
