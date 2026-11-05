/**
 * Visor de auditoría (RF-701) — sección colapsable con filtros y tabla.
 *
 * Reglas:
 *
 * - **Solo lectura**: la sección hace `GET` y nada más. No hay botón de
 *   borrar ni de editar, y la API no tiene esas rutas (el test del backend
 *   lo comprueba).
 * - **La nota la manda el backend**: `datos.nota` («Registros inmutables a
 *   nivel de base de datos (RF-701)») se pinta tal cual, sin redactarla
 *   aquí: la inmutabilidad es una propiedad del sistema.
 * - **Solo pide cuando se abre**: el panel no gasta una consulta de
 *   auditoría en cargar la página.
 * - Sin lenguaje de compra/venta: se leen registros, no «operaciones».
 */
import { useEffect, useState } from 'react'
import { ArrowsLeftRight, Database } from '@phosphor-icons/react'

import { obtener } from '../api/cliente.ts'
import type { Fase } from '../api/cliente.ts'
import { esquemaAuditoria } from '../api/esquemas.ts'
import type { Auditoria } from '../api/esquemas.ts'
import {
  AVISO_RANGO,
  LIMITE_PAGINA,
  offsetAnterior,
  offsetSiguiente,
  rutaAuditoria,
  textoPagina,
} from '../lib/auditoria.ts'
import type { FiltroAuditoria } from '../lib/auditoria.ts'

const CLASE_CAMPO =
  'rounded-md border border-border bg-background px-2 py-1.5 text-xs ' +
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring'

const CLASE_BOTON =
  'rounded-md border border-border px-3 py-1.5 text-xs font-medium ' +
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ' +
  'disabled:cursor-not-allowed disabled:opacity-60'

/** Columnas de la tabla, en el orden en que las manda la API. */
const COLUMNAS = ['Fecha', 'Acción', 'Actor', 'IP', 'Detalle'] as const

export function Auditoria() {
  const [abierto, setAbierto] = useState(false)
  const [actor, setActor] = useState('')
  const [accion, setAccion] = useState('')
  const [desde, setDesde] = useState('')
  const [hasta, setHasta] = useState('')
  const [filtro, setFiltro] = useState<FiltroAuditoria>({ limit: LIMITE_PAGINA })
  const [datos, setDatos] = useState<Auditoria | null>(null)
  const [fase, setFase] = useState<Fase>('cargando')
  const [mensaje, setMensaje] = useState<string | null>(null)

  // Una sola petición por cambio de filtro, cancelada al desmontar o al
  // volver a pulsar: nunca dos respuestas compitiendo por el mismo estado.
  useEffect(() => {
    if (!abierto) {
      return
    }
    let vivo = true
    const peticion = new AbortController()
    void (async () => {
      const respuesta = await obtener(rutaAuditoria(filtro), esquemaAuditoria, {
        signal: peticion.signal,
      })
      if (!vivo || respuesta.estado === 'cancelado') {
        return
      }
      if (respuesta.estado === 'ok') {
        setDatos(respuesta.datos)
        setFase('ok')
        setMensaje(null)
        return
      }
      setFase('error')
      setMensaje(respuesta.mensaje)
    })()
    return () => {
      vivo = false
      peticion.abort()
    }
  }, [abierto, filtro])

  function aplicar() {
    // Cualquier filtro nuevo vuelve a la primera página: offset en una
    // página que ya no existe es la forma más rápida de ver «0 de 0».
    setFase('cargando')
    setFiltro({ actor, accion, desde, hasta, limit: LIMITE_PAGINA, offset: 0 })
  }

  function limpiar() {
    setActor('')
    setAccion('')
    setDesde('')
    setHasta('')
    setFase('cargando')
    setFiltro({ limit: LIMITE_PAGINA })
  }

  function cambiarOffset(offset: number | null) {
    if (offset === null) {
      return
    }
    setFase('cargando')
    setFiltro((actual) => ({ ...actual, offset }))
  }

  const sinFechas = !desde && !hasta

  return (
    <section className="rounded-2xl border border-border bg-card p-6">
      <div className="flex items-center justify-between gap-4">
        <div className="min-w-0">
          <h2 className="text-sm font-medium">Auditoría del sistema</h2>
          <p className="text-xs text-muted-foreground">
            Quién hizo qué, desde qué equipo y cuándo. Solo lectura.
          </p>
        </div>
        <button
          type="button"
          onClick={() => {
            // Abrir es lo que dispara la primera consulta: el indicador se
            // pide aquí, en el evento, y no dentro del efecto.
            if (!abierto) {
              setFase('cargando')
            }
            setAbierto(!abierto)
          }}
          aria-expanded={abierto}
          className={`shrink-0 ${CLASE_BOTON} text-muted-foreground hover:bg-muted`}
        >
          {abierto ? 'Ocultar' : 'Ver auditoría'}
        </button>
      </div>

      {abierto ? (
        <div className="mt-4">
          <form
            className="flex flex-wrap items-end gap-3"
            onSubmit={(evento) => {
              evento.preventDefault()
              aplicar()
            }}
          >
            <label className="flex flex-col gap-1 text-xs text-muted-foreground">
              Actor
              <input
                value={actor}
                onChange={(evento) => setActor(evento.target.value)}
                placeholder="SISTEMA"
                className={`${CLASE_CAMPO} dato w-36`}
              />
            </label>
            <label className="flex flex-col gap-1 text-xs text-muted-foreground">
              Acción
              <input
                value={accion}
                onChange={(evento) => setAccion(evento.target.value)}
                placeholder="KILL_SWITCH_TRIGGERED"
                className={`${CLASE_CAMPO} dato w-64`}
              />
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
            <button type="submit" className={`${CLASE_BOTON} text-foreground`}>
              Aplicar
            </button>
            <button
              type="button"
              onClick={limpiar}
              className={`${CLASE_BOTON} text-muted-foreground`}
            >
              Limpiar
            </button>
          </form>

          <p className="mt-2 text-xs text-muted-foreground">
            {sinFechas ? AVISO_RANGO : 'Rango elegido: fechas de sesión (America/New_York).'}
          </p>

          {/* La nota la redacta el backend: es una propiedad del sistema. */}
          {datos ? (
            <p className="mt-3 flex items-center gap-1.5 rounded-md bg-muted px-3 py-2 text-xs text-foreground">
              <Database weight="bold" className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              <span>{datos.nota}</span>
            </p>
          ) : null}

          {fase === 'error' ? (
            <p className="mt-3 text-xs text-ambar-texto" role="status">
              {mensaje}
            </p>
          ) : null}

          {fase === 'cargando' && datos === null ? (
            <p className="mt-3 text-xs text-muted-foreground">Cargando registros…</p>
          ) : null}

          {datos ? (
            <>
              <div className="mt-3 overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead>
                    <tr className="border-b border-border text-muted-foreground">
                      {COLUMNAS.map((columna) => (
                        <th key={columna} scope="col" className="px-2 py-1.5 font-medium">
                          {columna}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {datos.registros.length === 0 ? (
                      <tr>
                        <td
                          colSpan={COLUMNAS.length}
                          className="px-2 py-4 text-muted-foreground"
                        >
                          Sin registros en ese rango.
                        </td>
                      </tr>
                    ) : (
                      datos.registros.map((registro) => (
                        <tr key={registro.id} className="border-b border-border/50">
                          <td className="dato px-2 py-1.5 whitespace-nowrap">
                            {registro.fecha}
                          </td>
                          <td className="dato px-2 py-1.5">{registro.action}</td>
                          <td className="dato px-2 py-1.5">{registro.actor}</td>
                          <td className="dato px-2 py-1.5 whitespace-nowrap">
                            {registro.ip_address}
                          </td>
                          <td className="dato px-2 py-1.5 max-w-[28rem] truncate">
                            <span title={registro.payload ?? ''}>
                              {registro.payload ?? '—'}
                            </span>
                          </td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>

              <div className="mt-3 flex items-center justify-between gap-3">
                <p className="text-xs text-muted-foreground">
                  {textoPagina(datos.offset, datos.limit, datos.total)}
                </p>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => cambiarOffset(offsetAnterior(datos.offset, datos.limit))}
                    disabled={offsetAnterior(datos.offset, datos.limit) === null}
                    className={`${CLASE_BOTON} text-muted-foreground`}
                  >
                    Anterior
                  </button>
                  <button
                    type="button"
                    onClick={() =>
                      cambiarOffset(offsetSiguiente(datos.offset, datos.limit, datos.total))
                    }
                    disabled={
                      offsetSiguiente(datos.offset, datos.limit, datos.total) === null
                    }
                    className={`${CLASE_BOTON} text-muted-foreground`}
                  >
                    Siguiente
                  </button>
                </div>
              </div>
            </>
          ) : null}

          <p className="mt-3 flex items-center gap-1.5 text-xs text-muted-foreground">
            <ArrowsLeftRight weight="bold" className="h-3.5 w-3.5" aria-hidden="true" />
            Coincidencia exacta en actor y acción; el rango se refiere a fechas de sesión.
          </p>
        </div>
      ) : null}
    </section>
  )
}
