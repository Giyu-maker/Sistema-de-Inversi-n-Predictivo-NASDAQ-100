/**
 * Pantalla de operación de la Fase B2: cabecera fija (estado, motivo,
 * antigüedad, franja de sesión y tema), tarjeta de señal, gráfico de velas,
 * rendimiento e historial paginado, enganchados a los hooks de datos de B2.2.
 * Contrato de dirección en `.impeccable/surfaces/frontend-src-app-tsx.md`.
 *
 * B2.4: Recharts llega en un chunk diferido (`React.lazy`) — la cabecera y
 * la tarjeta de señal no esperan a que el gráfico se descargue, y mientras
 * tanto se ve un estado de «cargando» con la misma altura para no saltar.
 */
import { lazy, Suspense } from 'react'

import { Cabecera } from './components/cabecera.tsx'
import { Historial } from './components/historial.tsx'
import { Rendimiento } from './components/rendimiento.tsx'
import { TarjetaSenal } from './components/tarjeta-senal.tsx'
import { bannerPruebas } from './api/pruebas.ts'
import { llaveDe, useCiclo } from './hooks/useCiclo.ts'
import { useHistorial } from './hooks/useHistorial.ts'
import { useEstado } from './hooks/useEstado.ts'

// Única dependencia nueva de B2.4 (backlog Ítem 8, RF-05): pesa lo justo y
// viaja aparte para no retrasar el primer pintado (RNF-004).
const GraficoVelas = lazy(() => import('./components/grafico-velas.tsx'))

/** Reserva de altura del gráfico para que el chunk no mueva el layout. */
function CargandoGrafico() {
  return (
    <section aria-busy="true" className="rounded-2xl border border-border bg-card p-6">
      <div className="flex items-baseline justify-between gap-4">
        <h2 className="text-sm font-medium text-muted-foreground">Velas de 5 minutos · QQQ</h2>
      </div>
      <div className="mt-3 h-[280px] animate-pulse rounded-lg bg-muted motion-reduce:animate-none" />
      <p className="mt-3 text-sm text-muted-foreground">Cargando gráfico…</p>
    </section>
  )
}

export default function App() {
  const banner = bannerPruebas()
  const { estado, fase, mensaje } = useEstado()
  const llave = llaveDe(estado)
  const ciclo = useCiclo(llave)
  const historial = useHistorial(llave)

  return (
    <div className="min-h-dvh bg-background text-foreground">
      {banner ? banner() : null}
      <Cabecera estado={estado} fase={fase} mensaje={mensaje} />
      <main className="mx-auto max-w-5xl px-6 py-8">
        {estado === null ? (
          <p className="text-sm text-muted-foreground">
            {fase === 'error'
              ? (mensaje ?? 'No se pudo contactar con el servidor.')
              : 'Esperando la primera respuesta del sistema…'}
          </p>
        ) : (
          <div className="grid grid-cols-12 gap-4">
            <div className="col-span-12 lg:col-span-5">
              <TarjetaSenal estado={estado} calidad={ciclo.datos?.calidad ?? null} />
            </div>
            <div className="col-span-12 lg:col-span-7">
              <Suspense fallback={<CargandoGrafico />}>
                <GraficoVelas
                  velas={ciclo.datos?.velas ?? null}
                  cargando={ciclo.fase === 'cargando' && ciclo.datos === null}
                  mensaje={ciclo.mensaje}
                />
              </Suspense>
            </div>
            <div className="col-span-12 lg:col-span-5">
              <Rendimiento rendimiento={ciclo.datos?.rendimiento ?? null} />
            </div>
            <div className="col-span-12 lg:col-span-7">
              <Historial historial={historial} />
            </div>
          </div>
        )}
      </main>
    </div>
  )
}
