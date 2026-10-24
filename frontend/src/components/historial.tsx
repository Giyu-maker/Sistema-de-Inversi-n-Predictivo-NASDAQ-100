/**
 * Historial paginado de predicciones (B2.4).
 *
 * Reglas del brief de la Fase B2, todas acá:
 *
 * - **Datos reales del endpoint** `/api/predictions` (hook `useHistorial`):
 *   filas, avisos del backend y «cargar más» sin duplicados.
 * - **Lenguaje llano**: los enums del contrato (UP/DOWN/STABLE y
 *   DISPONIBLE/PENDIENTE/NO_APLICA) se pintan como palabras — el mismo
 *   criterio del chip de estado en la cabecera: el backend manda el enum y su
 *   `motivo`, aquí solo se pinta la etiqueta.
 * - **Resultado neutro**: el retorno va firmado (+/−) en tinta neutra, nunca
 *   verde ni rojo (el rojo lo reserva el Kill-Switch).
 * - **Sin jerga**: la confianza se dice «Confianza 86,0 %», no «score» ni
 *   «probabilidad».
 *
 * Fila de dos líneas para que quepa sin desbordes a 390px: arriba fecha y
 * resultado, abajo tendencia y confianza.
 */
import {
  ArrowDown,
  ArrowUp,
  Info,
  Minus,
  Warning,
} from '@phosphor-icons/react'

import type { Prediccion } from '../api/esquemas.ts'
import type { UsoHistorial } from '../hooks/useHistorial.ts'
import { formatearPorcentajeFirmado, formatearRatio } from '../lib/formato.ts'

/** Palabras llanas para la tendencia predicha (el enum lo manda el backend). */
const TENDENCIAS: Record<string, { texto: string; clase: string }> = {
  UP: { texto: 'Al alza', clase: 'text-verde-texto' },
  DOWN: { texto: 'A la baja', clase: 'text-ambar-texto' },
  STABLE: { texto: 'Sin movimiento', clase: 'text-foreground' },
}

/** Palabras llanas para la dirección real observada en el resultado. */
const DIRECCIONES: Record<string, string> = {
  UP: 'subió',
  DOWN: 'bajó',
  STABLE: 'sin movimiento',
}

function iconoTendencia(codigo: string): React.ReactElement {
  if (codigo === 'UP') return <ArrowUp weight="bold" className="h-4 w-4" aria-hidden="true" />
  if (codigo === 'DOWN') return <ArrowDown weight="bold" className="h-4 w-4" aria-hidden="true" />
  return <Minus weight="bold" className="h-4 w-4" aria-hidden="true" />
}

/**
 * Resultado real de la fila en lenguaje llano y tinta neutra: retorno
 * firmado + dirección observada; los estados sin retorno se pintan como
 * palabras («Pendiente», «No aplica»).
 */
function pintarResultado(prediccion: Prediccion): React.ReactNode {
  const resultado = prediccion.resultado_real
  if (resultado === null) {
    return <span className="text-muted-foreground">—</span>
  }
  if (resultado.estado === 'DISPONIBLE' && resultado.retorno_pct !== null) {
    const direccion =
      resultado.etiqueta !== null
        ? (DIRECCIONES[resultado.etiqueta] ?? resultado.etiqueta.toLowerCase())
        : null
    return (
      <>
        <span className="dato">{formatearPorcentajeFirmado(resultado.retorno_pct)}</span>
        {direccion !== null ? (
          <span className="text-muted-foreground"> · {direccion}</span>
        ) : null}
      </>
    )
  }
  if (resultado.estado === 'PENDIENTE') {
    return <span className="text-muted-foreground">Pendiente</span>
  }
  if (resultado.estado === 'NO_APLICA') {
    return <span className="text-muted-foreground">No aplica</span>
  }
  // Estado desconocido (contrato nuevo): se muestra el valor crudo sin
  // inventar una traducción — el backend es quien debe dar su palabra.
  return <span className="dato text-muted-foreground">{resultado.estado}</span>
}

interface PropsHistorial {
  historial: UsoHistorial
}

export function Historial({ historial }: PropsHistorial) {
  const { filas, total, fase, mensaje, cargandoMas, puedeCargarMas, cargarMas, refrescar, avisos } =
    historial

  const primerCarga = fase === 'cargando' && filas.length === 0

  return (
    <section aria-labelledby="titulo-historial" className="rounded-2xl border border-border bg-card p-6">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 id="titulo-historial" className="text-sm font-medium text-muted-foreground">
          Historial de predicciones
        </h2>
        <p className="text-xs text-muted-foreground">
          Mostrando <span className="dato">{filas.length}</span> de{' '}
          <span className="dato">{total}</span> predicciones
        </p>
      </div>

      {/* Avisos del backend (p. ej. precios históricos ilegibles): conviene
          saberlos, no deciden nada. Pueden traer una excepción larga con URLs
          y sin espacios (replican el copy real de B1), así que el texto va en
          un `span` que puede encoger y cortar palabras: sin esto se sale de la
          tarjeta a 390 px (break-ui B2.5). */}
      {avisos.map((aviso) => (
        <p key={aviso} className="mt-2 flex items-start gap-1.5 text-xs text-muted-foreground">
          <Info weight="bold" className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          <span className="min-w-0 break-words">{aviso}</span>
        </p>
      ))}

      {fase === 'error' ? (
        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-ambar-texto">
          <p className="flex items-start gap-1.5">
            <Warning weight="bold" className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            <span className="min-w-0 break-words">
              {mensaje ?? 'No se pudo cargar el historial.'}
            </span>
          </p>
          <button
            type="button"
            onClick={refrescar}
            className="text-sm font-medium underline underline-offset-4 focus-visible:ring-2 focus-visible:ring-ring"
          >
            Reintentar
          </button>
        </div>
      ) : null}

      {primerCarga ? (
        <p className="mt-4 text-sm text-muted-foreground">Cargando historial…</p>
      ) : filas.length === 0 && fase === 'ok' ? (
        <p className="mt-4 text-sm text-muted-foreground">Sin predicciones registradas todavía.</p>
      ) : (
        <>
          <ul className="mt-3">
            {filas.map((prediccion) => {
              const tendencia = TENDENCIAS[prediccion.trend_prediction]
              return (
                <li key={prediccion.id} className="border-b border-border py-2.5 last:border-0">
                  <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                    <span className="dato text-xs text-muted-foreground">{prediccion.fecha}</span>
                    <span className="text-sm">{pintarResultado(prediccion)}</span>
                  </div>
                  <div className="mt-0.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
                    <span className={`flex items-center gap-1.5 ${tendencia?.clase ?? 'text-muted-foreground'}`}>
                      {iconoTendencia(prediccion.trend_prediction)}
                      {tendencia?.texto ?? prediccion.trend_prediction}
                    </span>
                    <span className="text-muted-foreground">
                      Confianza{' '}
                      <span className="dato text-foreground">
                        {formatearRatio(prediccion.confidence_score)}
                      </span>
                    </span>
                  </div>
                </li>
              )
            })}
          </ul>

          <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
            {puedeCargarMas ? (
              <button
                type="button"
                onClick={cargarMas}
                disabled={cargandoMas}
                aria-busy={cargandoMas}
                className="rounded-md border border-border px-3 py-1.5 text-sm focus-visible:ring-2 focus-visible:ring-ring hover:bg-muted disabled:opacity-60"
              >
                {cargandoMas ? 'Cargando…' : 'Cargar más'}
              </button>
            ) : (
              <span className="text-xs text-muted-foreground">Fin del historial.</span>
            )}
          </div>
        </>
      )}
    </section>
  )
}
