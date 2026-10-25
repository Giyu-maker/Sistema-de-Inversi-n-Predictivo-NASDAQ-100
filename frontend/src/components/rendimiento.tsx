/**
 * Rendimiento virtual de la sesión (B2.4).
 *
 * Reglas del brief de la Fase B2, todas acá:
 *
 * - **Signo +/− neutro**: `formatearPorcentajeFirmado` firma siempre la cifra
 *   («+0,56 %» / «−2,14 %») pero el color es tinta neutra, nunca verde ni
 *   rojo: el rojo lo reserva el Kill-Switch (SRS §10.3) y este número es
 *   virtual, sin capital en juego.
 * - **La nota «virtual, sin capital» siempre visible**: no vive en un
 *   desplegable ni en un tooltip; el operador no debe poder confundir estas
 *   cifras con un resultado real.
 * - **Sin jerga**: «apalancamiento 2×», no «beta» ni «efecto apalancado».
 *   Los textos de estado (`motivo`, `nota`) vienen en llano del backend.
 */
import { Warning } from '@phosphor-icons/react'

import type { Rendimiento as DatosRendimiento } from '../api/esquemas.ts'
import {
  formatearNumero,
  formatearPorcentaje,
  formatearPorcentajeFirmado,
} from '../lib/formato.ts'

interface PropsRendimiento {
  rendimiento: DatosRendimiento | null
}

export function Rendimiento({ rendimiento }: PropsRendimiento) {
  if (rendimiento === null) {
    return (
      <section aria-labelledby="titulo-rendimiento" className="rounded-2xl border border-border bg-card p-6">
        <h2 id="titulo-rendimiento" className="text-sm font-medium text-muted-foreground">
          Rendimiento de la sesión
        </h2>
        <p className="mt-2 text-sm text-muted-foreground">Esperando los precios de la sesión…</p>
      </section>
    )
  }

  const sinDatos = !rendimiento.disponible || rendimiento.rendimiento_ritech_pct === null

  return (
    <section aria-labelledby="titulo-rendimiento" className="rounded-2xl border border-border bg-card p-6">
      <div className="flex items-baseline justify-between gap-x-4">
        <h2 id="titulo-rendimiento" className="text-sm font-medium text-muted-foreground">
          Rendimiento de la sesión
        </h2>
        {rendimiento.ultima_vela !== null ? (
          <span className="dato text-xs text-muted-foreground">{rendimiento.ultima_vela.fecha}</span>
        ) : null}
      </div>

      {sinDatos ? (
        <p className="mt-3 text-sm text-muted-foreground">
          {rendimiento.motivo ?? 'Sin rendimiento calculado todavía.'}
        </p>
      ) : (
        <>
          <p className="cifra mt-3 text-5xl leading-none">
            {formatearPorcentajeFirmado(rendimiento.rendimiento_ritech_pct ?? 0)}
          </p>
          <p className="mt-2 text-sm text-muted-foreground">
            Rendimiento virtual de RiTech sobre la inversión de la sesión.
          </p>

          <dl className="mt-4 space-y-2 text-sm">
            <div className="flex items-baseline justify-between gap-3 border-b border-border pb-1">
              <dt className="text-muted-foreground">Variación de QQQ</dt>
              <dd className="dato">
                {formatearPorcentajeFirmado(rendimiento.variacion_nasdaq_pct ?? 0)}
              </dd>
            </div>
            <div className="flex items-baseline justify-between gap-3 border-b border-border pb-1">
              <dt className="text-muted-foreground">Apalancamiento</dt>
              <dd className="dato">2×</dd>
            </div>
            {rendimiento.apertura_sesion !== null && rendimiento.ultima_vela !== null ? (
              <div className="flex items-baseline justify-between gap-3 border-b border-border pb-1">
                <dt className="text-muted-foreground">Apertura → última vela</dt>
                <dd className="dato">
                  {formatearNumero(rendimiento.apertura_sesion.precio)} →{' '}
                  {formatearNumero(rendimiento.ultima_vela.precio)}
                </dd>
              </div>
            ) : null}
            <div className="flex items-baseline justify-between gap-3">
              <dt className="text-muted-foreground">Límite teórico de pérdida</dt>
              <dd className="dato">{formatearPorcentaje(rendimiento.limite_perdida_pct, 2)}</dd>
            </div>
          </dl>

          {rendimiento.umbral_perdida_alcanzado ? (
            <p className="mt-3 flex items-start gap-2 text-sm text-ambar-texto">
              <Warning weight="bold" className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
              Límite teórico de pérdida alcanado en la sesión.
            </p>
          ) : null}
        </>
      )}

      {/* La nota «virtual» vive siempre visible, sea cual sea el estado. */}
      <p className="mt-4 border-t border-border pt-3 text-xs text-muted-foreground">{rendimiento.nota}</p>
    </section>
  )
}
