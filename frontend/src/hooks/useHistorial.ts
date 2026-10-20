/**
 * Historial paginado de predicciones (`/api/predictions`).
 *
 * - La página 0 se vuelve a pedir cuando cambia la llave (vela nueva): una
 *   predicción recién registrada aparece sin que el operador toque nada.
 * - «Cargar más» pide el siguiente tramo con `offset = filas.length` y
 *   agrega **sin duplicar** (deduplicado por `id`: si el backend recibe una
 *   fila nueva en plena paginación, no puede aparecer dos veces).
 * - Solo hay una petición de lista en vuelo a la vez: «cargar más» mientras
 *   hay una en curso se ignora, y la página 0 —la de la vela nueva o del
 *   refresco— cancela la anterior en lugar de esperarla.
 */
import { useCallback, useEffect, useRef, useState } from 'react'

import { obtener } from '../api/cliente.ts'
import type { Fase } from '../api/cliente.ts'
import { esquemaPrediccionPagina } from '../api/esquemas.ts'
import type { Prediccion } from '../api/esquemas.ts'

export const POR_PAGINA = 20

export interface UsoHistorial {
  filas: Prediccion[]
  /** Total de predicciones registradas (todas las páginas). */
  total: number
  fase: Fase
  mensaje: string | null
  cargandoMas: boolean
  puedeCargarMas: boolean
  /** Pide el siguiente tramo y lo agrega (sin duplicar). */
  cargarMas: () => void
  /** Reinicia en la página 0 (misma llave). */
  refrescar: () => void
}

function rutaDe(offset: number): string {
  return `/api/predictions?limit=${POR_PAGINA}&offset=${offset}`
}

/** Reemplaza la lista o agrega sin duplicar por `id`. */
function mezclar(previas: Prediccion[], nuevas: Prediccion[], agregar: boolean): Prediccion[] {
  if (!agregar) {
    return nuevas
  }
  const conocidas = new Set(previas.map((fila) => fila.id))
  return [...previas, ...nuevas.filter((p) => !conocidas.has(p.id))]
}

export function useHistorial(llave: number | null | undefined): UsoHistorial {
  const [filas, setFilas] = useState<Prediccion[]>([])
  const [total, setTotal] = useState(0)
  const [fase, setFase] = useState<Fase>('cargando')
  const [mensaje, setMensaje] = useState<string | null>(null)
  const [cargandoMas, setCargandoMas] = useState(false)
  const [impulso, setImpulso] = useState(0)
  const refrescar = useCallback(() => setImpulso((n) => n + 1), [])
  const peticion = useRef<AbortController | null>(null)
  const enVuelo = useRef(false)

  // Página 0: con cada vela nueva o refresco manual. Conserva lo cargado si
  // falla (una página perdida no debe vaciar lo que ya se está leyendo).
  useEffect(() => {
    if (llave === undefined) {
      return
    }
    let vivo = true
    enVuelo.current = true
    peticion.current?.abort()
    const control = new AbortController()
    peticion.current = control

    void (async () => {
      const respuesta = await obtener(rutaDe(0), esquemaPrediccionPagina, {
        signal: control.signal,
      })
      enVuelo.current = false
      if (!vivo || respuesta.estado === 'cancelado') {
        return
      }
      if (respuesta.estado === 'ok') {
        setTotal(respuesta.datos.total)
        setFase('ok')
        setMensaje(null)
        setFilas((previas) => mezclar(previas, respuesta.datos.predicciones, false))
      } else {
        setFase('error')
        setMensaje(respuesta.mensaje)
      }
    })()

    return () => {
      vivo = false
      control.abort()
    }
  }, [llave, impulso])

  const cargarMas = useCallback(() => {
    if (enVuelo.current || filas.length >= total) {
      return
    }
    const offset = filas.length
    enVuelo.current = true
    peticion.current?.abort()
    const control = new AbortController()
    peticion.current = control
    setCargandoMas(true)

    void (async () => {
      const respuesta = await obtener(rutaDe(offset), esquemaPrediccionPagina, {
        signal: control.signal,
      })
      enVuelo.current = false
      setCargandoMas(false)
      if (respuesta.estado === 'cancelado') {
        return
      }
      if (respuesta.estado === 'ok') {
        setTotal(respuesta.datos.total)
        setFase('ok')
        setMensaje(null)
        setFilas((previas) => mezclar(previas, respuesta.datos.predicciones, true))
      } else {
        setFase('error')
        setMensaje(respuesta.mensaje)
      }
    })()
  }, [filas.length, total])

  return {
    filas,
    total,
    fase,
    mensaje,
    cargandoMas,
    puedeCargarMas: filas.length < total,
    cargarMas,
    refrescar,
  }
}
