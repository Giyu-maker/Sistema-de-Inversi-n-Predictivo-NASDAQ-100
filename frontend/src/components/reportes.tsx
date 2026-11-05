/**
 * Sección «Reportes» del panel (RF-702) — selector de tipo/rango y descarga.
 *
 * Reglas:
 *
 * - **Secciones colapsables, sin router ni dependencias nuevas** (decisión
 *   P4 del plan): el panel es de una sola pantalla y las dos vistas nuevas
 *   (reportes y, en D4, auditoría) entran como secciones que se abren.
 * - **La descarga es un enlace**, no una petición del cliente: el navegador
 *   recibe el `Content-Disposition` de la API y guarda el archivo. Así no
 *   hace falta un helper de blobs ni Zod para un cuerpo que no es JSON.
 * - **Sin lenguaje de compra/venta**: los nombres de los reportes describen
 *   datos («señales», «rendimiento», «eventos»), nunca órdenes.
 * - Las fechas vacías no se envían: el servidor aplica su rango por
 *   defecto (últimas 5 sesiones) y el aviso lo dice.
 */
import { FileCsv, FilePdf } from '@phosphor-icons/react'
import { useState } from 'react'

import {
  AVISO_RANGO_POR_DEFECTO,
  FORMATOS_REPORTE,
  TIPOS_REPORTE,
  rutaReporte,
} from '../lib/reportes.ts'
import type { FormatoReporte, TipoReporte } from '../lib/reportes.ts'

const CLASE_CAMPO =
  'rounded-md border border-border bg-background px-2 py-1.5 text-xs ' +
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'

export function Reportes() {
  const [abierto, setAbierto] = useState(false)
  const [tipo, setTipo] = useState<TipoReporte>('senal')
  const [desde, setDesde] = useState('')
  const [hasta, setHasta] = useState('')

  const rango = { desde, hasta }
  const sinFechas = !desde && !hasta

  return (
    <section className="rounded-2xl border border-border bg-card p-6">
      <div className="flex items-center justify-between gap-4">
        <div className="min-w-0">
          <h2 className="text-sm font-medium">Reportes financieros</h2>
          <p className="text-xs text-muted-foreground">
            CSV y PDF generados en el servidor, con su cabecera y la métrica
            del modelo.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setAbierto((valor) => !valor)}
          aria-expanded={abierto}
          className="shrink-0 rounded-md border border-border px-3 py-1.5 text-xs font-medium text-muted-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {abierto ? 'Ocultar' : 'Ver reportes'}
        </button>
      </div>

      {abierto ? (
        <div className="mt-4 flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Tipo de reporte
            <select
              value={tipo}
              onChange={(evento) => setTipo(evento.target.value as TipoReporte)}
              className={`${CLASE_CAMPO} w-64`}
            >
              {TIPOS_REPORTE.map((opcion) => (
                <option key={opcion.id} value={opcion.id}>
                  {opcion.nombre}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Desde (fecha de sesión)
            <input
              type="date"
              value={desde}
              onChange={(evento) => setDesde(evento.target.value)}
              className={`${CLASE_CAMPO} dato`}
            />
          </label>

          <label className="flex flex-col gap-1 text-xs text-muted-foreground">
            Hasta (fecha de sesión)
            <input
              type="date"
              value={hasta}
              onChange={(evento) => setHasta(evento.target.value)}
              className={`${CLASE_CAMPO} dato`}
            />
          </label>

          <div className="flex gap-2">
            {FORMATOS_REPORTE.map((formato) => (
              <a
                key={formato}
                href={rutaReporte(tipo, rango, formato as FormatoReporte)}
                download
                className="inline-flex items-center gap-1.5 rounded-md border border-border px-3 py-1.5 text-xs font-medium text-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {formato === 'csv' ? (
                  <FileCsv weight="bold" className="h-4 w-4" aria-hidden="true" />
                ) : (
                  <FilePdf weight="bold" className="h-4 w-4" aria-hidden="true" />
                )}
                Descargar {formato.toUpperCase()}
              </a>
            ))}
          </div>
        </div>
      ) : null}

      {abierto ? (
        <p className="mt-3 text-xs text-muted-foreground">
          {sinFechas
            ? AVISO_RANGO_POR_DEFECTO
            : 'Rango elegido: las fechas son de sesión (America/New_York).'}
        </p>
      ) : null}
    </section>
  )
}
