/**
 * Tarjeta de señal (B2.3): el veredicto de la predicción en lenguaje llano.
 *
 * Reglas del brief de la Fase B2, todas acá:
 *
 * - **Sin «compra» ni «vende»**: el sistema decide en el backend (D7) y aquí
 *   solo se pinta «Señal operable» o su motivo; la palabra la pone
 *   `senal.motivo`, que viene en llano del backend.
 * - **UP/DOWN ⇒ «la regla del umbral»**: la confianza en serif grande sobre
 *   una regla con el tick del umbral del perfil en la misma escala; el
 *   marcador solo se enmarca cuando la confianza lo cruza (`senal.operable`).
 *   Es el único movimiento firma del panel y respeta `prefers-reduced-motion`.
 * - **STABLE ⇒ «Sin movimiento esperado»**, con su confianza pero SIN tick ni
 *   umbral: no hay regla que cruzar.
 * - **Confianza en texto llano**: «tendencia con baja confianza», nunca
 *   «28 % de probabilidad de subida» (el modelo no expone probabilidades).
 * - **XAI en 1–2 líneas con «ver detalle»**: el detalle técnico despliega el
 *   `technical_rationale` completo; la jerga vive solo ahí.
 * - **En cerrado**: «Predicción de la sesión anterior» con su fecha; el
 *   mercado cerrado es el estado normal, no un error.
 * - **Franja de calidad pegada a la tarjeta**, con panel desplegable para lo
 *   técnico (macro-F1, dispositivo, entrenado…).
 */
import { useState } from 'react'
import {
  ArrowDown,
  ArrowUp,
  CaretDown,
  Minus,
} from '@phosphor-icons/react'

import type { CalidadModelo, EstadoSistema } from '../api/esquemas.ts'
import { ESPACIO_FINO, formatearRatio } from '../lib/formato.ts'

/** Confianza por debajo de la cual el contrato exige decirlo en llano. */
const CONFIANZA_BAJA = 0.5

interface PropsTarjetaSenal {
  estado: EstadoSistema
  calidad: CalidadModelo | null
}

export function TarjetaSenal({ estado, calidad }: PropsTarjetaSenal) {
  const [detalleAbierto, setDetalleAbierto] = useState(false)
  const [calidadAbierta, setCalidadAbierta] = useState(false)

  const prediccion = estado.ultima_prediccion
  const senal = estado.senal
  const enCerrado = !estado.mercado.abierto

  const tendencia = prediccion?.trend_prediction ?? null
  const confianza = prediccion?.confidence_score ?? null
  const esTendencia = tendencia === 'UP' || tendencia === 'DOWN'

  const veredicto = esTendencia ? (
    tendencia === 'UP' ? (
      <span className="inline-flex items-center gap-2 text-verde-texto">
        <ArrowUp weight="bold" className="h-7 w-7" aria-hidden="true" />
        Tendencia al alza
      </span>
    ) : (
      <span className="inline-flex items-center gap-2 text-ambar-texto">
        <ArrowDown weight="bold" className="h-7 w-7" aria-hidden="true" />
        Tendencia a la baja
      </span>
    )
  ) : (
    <span className="inline-flex items-center gap-2 text-foreground">
      <Minus weight="bold" className="h-7 w-7" aria-hidden="true" />
      Sin movimiento esperado
    </span>
  )

  const confianzaBaja =
    confianza !== null && esTendencia && confianza < CONFIANZA_BAJA

  return (
    <section aria-labelledby="titulo-senal" className="rounded-2xl border border-border bg-card p-6">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 id="titulo-senal" className="text-sm font-medium text-muted-foreground">
          {enCerrado ? 'Predicción de la sesión anterior' : 'Predicción de la sesión'}
        </h2>
        {prediccion !== null && prediccion !== undefined ? (
          <span className="dato text-xs text-muted-foreground">{prediccion.fecha}</span>
        ) : null}
      </div>

      {prediccion === null || prediccion === undefined ? (
        <p className="mt-4 text-sm text-muted-foreground">
          {senal?.motivo ?? 'Sin predicción registrada todavía.'}
        </p>
      ) : (
        <>
          <p className="mt-3 text-2xl font-semibold">{veredicto}</p>

          <div className="mt-4 flex items-end gap-4">
            <p className="cifra text-5xl leading-none">
              {formatearRatio(confianza ?? 0)}
            </p>
            <p className="pb-1 text-sm text-muted-foreground">
              {confianzaBaja
                ? 'Tendencia con baja confianza.'
                : 'Confianza del modelo en esta tendencia.'}
            </p>
          </div>

          {/* La regla del umbral: solo UP/DOWN. STABLE no tiene umbral que
              cruzar, y sin predicción no hay nada que medir. */}
          {esTendencia && senal !== null && senal !== undefined ? (
            <div
              className={`mt-6 ${senal.operable ? 'rounded-xl border border-border bg-secondary/60 p-4' : 'py-1'}`}
            >
              <div className="relative h-4" aria-hidden="true">
                <div className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 bg-border" />
                <div
                  className="absolute top-0 h-4 w-px bg-muted-foreground"
                  style={{ left: `${senal.umbral * 100}%` }}
                />
                <div
                  className="absolute top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rotate-45 bg-foreground transition-[left] duration-500 ease-out motion-reduce:transition-none"
                  style={{ left: `${Math.min(confianza ?? 0, 1) * 100}%` }}
                />
              </div>
              <div className="mt-2 flex justify-between text-xs text-muted-foreground">
                <span>
                  Umbral del perfil: <span className="dato">{formatearRatio(senal.umbral)}</span>
                </span>
                <span>
                  Confianza actual:{' '}
                  <span className="dato">{formatearRatio(confianza ?? 0)}</span>
                </span>
              </div>
            </div>
          ) : null}

          <p className="mt-4 text-sm text-foreground">{senal?.motivo}</p>

          {/* XAI: 1–2 líneas en llano (viene del backend), detalle completo
              tras «ver detalle». */}
          <div className="mt-4">
            <p className={detalleAbierto ? '' : 'line-clamp-2'}>
              {prediccion.technical_rationale}
            </p>
            <button
              type="button"
              onClick={() => setDetalleAbierto((abierto) => !abierto)}
              aria-expanded={detalleAbierto}
              className="mt-2 inline-flex items-center gap-1 text-sm font-medium text-foreground underline-offset-4 hover:underline focus-visible:ring-2 focus-visible:ring-ring"
            >
              {detalleAbierto ? 'Ocultar detalle' : 'Ver detalle'}
              <CaretDown
                weight="bold"
                className={`h-3.5 w-3.5 transition-transform motion-reduce:transition-none ${detalleAbierto ? 'rotate-180' : ''}`}
                aria-hidden="true"
              />
            </button>
          </div>
        </>
      )}

      {/* Franja de calidad pegada a la tarjeta: resumen en llano, detalle
          técnico solo tras pedirlo. */}
      <div className="mt-6 border-t border-border pt-4">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <p className="text-sm font-medium text-muted-foreground">Calidad del modelo</p>
          {calidad !== null && calidad.disponible ? (
            <button
              type="button"
              onClick={() => setCalidadAbierta((abierta) => !abierta)}
              aria-expanded={calidadAbierta}
              className="inline-flex items-center gap-1 text-sm font-medium text-foreground underline-offset-4 hover:underline focus-visible:ring-2 focus-visible:ring-ring"
            >
              {calidadAbierta ? 'Ocultar detalle' : 'Ver detalle'}
              <CaretDown
                weight="bold"
                className={`h-3.5 w-3.5 transition-transform motion-reduce:transition-none ${calidadAbierta ? 'rotate-180' : ''}`}
                aria-hidden="true"
              />
            </button>
          ) : null}
        </div>
        {calidad === null ? (
          <p className="mt-1 text-sm text-muted-foreground">
            Aún sin datos de calidad del modelo.
          </p>
        ) : calidad.disponible ? (
          <>
            <p className="mt-1 text-sm">
              Entrenado el {calidad.entrenado_en ?? '—'} en{' '}
              {calidad.dispositivo_entrenamiento ?? '—'}.
            </p>
            {calidadAbierta ? (
              <dl className="mt-3 grid grid-cols-1 gap-x-8 gap-y-2 text-sm sm:grid-cols-2">
                <div className="flex justify-between gap-3 border-b border-border pb-1">
                  <dt className="text-muted-foreground">Tipo de modelo</dt>
                  <dd className="dato">{calidad.model_type ?? '—'}</dd>
                </div>
                <div className="flex justify-between gap-3 border-b border-border pb-1">
                  <dt className="text-muted-foreground">Versión de características</dt>
                  <dd className="dato">{calidad.feature_schema_version ?? '—'}</dd>
                </div>
                <div className="flex justify-between gap-3 border-b border-border pb-1">
                  <dt className="text-muted-foreground">Macro-F1 (prueba)</dt>
                  <dd className="dato">
                    {calidad.macro_f1_test === null ? '—' : formatearRatio(calidad.macro_f1_test)}
                  </dd>
                </div>
                <div className="flex justify-between gap-3 border-b border-border pb-1">
                  <dt className="text-muted-foreground">Banda sin movimiento</dt>
                  <dd className="dato">
                    {calidad.banda_stable_pct === null
                      ? '—'
                      : `${calidad.banda_stable_pct}${ESPACIO_FINO}%`}
                  </dd>
                </div>
                <div className="flex justify-between gap-3 border-b border-border pb-1">
                  <dt className="text-muted-foreground">Acierto direccional</dt>
                  <dd className="dato">
                    {calidad.acierto_direccional?.ratio === null ||
                    calidad.acierto_direccional?.ratio === undefined
                      ? '—'
                      : `${formatearRatio(calidad.acierto_direccional.ratio)} (${calidad.acierto_direccional.n ?? '—'} predicciones)`}
                  </dd>
                </div>
                <div className="flex justify-between gap-3 border-b border-border pb-1">
                  <dt className="text-muted-foreground">Dispositivo</dt>
                  <dd className="dato">{calidad.dispositivo_entrenamiento ?? '—'}</dd>
                </div>
              </dl>
            ) : null}
            <p className="mt-2 text-xs text-muted-foreground">{calidad.nota}</p>
          </>
        ) : (
          <p className="mt-1 text-sm text-muted-foreground">{calidad.motivo}</p>
        )}
      </div>
    </section>
  )
}
