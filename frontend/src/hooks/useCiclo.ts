/**
 * El ciclo de mercado: velas, predicción, rendimiento y calidad del modelo.
 *
 * Regla del brief de la Fase B2: **nada se re-pide por intervalo fijo**. El
 * ciclo se re-ejecuta solo cuando cambia la llave — el timestamp de la
 * última vela que trae el latido — o cuando el operador pide refrescar. Con
 * el mercado cerrado la llave no cambia y la red se calla; con el mercado
 * abierto, cada 5 minutos.
 *
 * **Atomicidad**: las cuatro rutas se piden juntas y el estado se sustituye
 * entero. Si una falla, se conserva el ciclo anterior completo y la fase pasa
 * a `error` con el mensaje: nunca conviven datos de dos momentos distintos.
 */
import { useCallback, useEffect, useState } from 'react'

import { MENSAJE_RED, obtener } from '../api/cliente.ts'
import type { Fase } from '../api/cliente.ts'
import {
  esquemaCalidadModelo,
  esquemaPrediccionUltima,
  esquemaRendimiento,
  esquemaVelas,
} from '../api/esquemas.ts'
import type {
  CalidadModelo,
  EstadoSistema,
  PrediccionUltima,
  Rendimiento,
  Velas,
} from '../api/esquemas.ts'

export interface DatosCiclo {
  velas: Velas
  ultima: PrediccionUltima
  rendimiento: Rendimiento
  calidad: CalidadModelo
}

export interface UsoCiclo {
  datos: DatosCiclo | null
  fase: Fase
  mensaje: string | null
  refrescar: () => void
}

/**
 * Llave del ciclo a partir del latido: el timestamp de la última vela.
 * `undefined` = el estado aún no cargó (no se pide nada todavía);
 * `null` = el estado cargó pero no hay velas (se pide una vez: los endpoints
 * responden vacío y la UI puede mostrar su estado de «sin datos»).
 */
export function llaveDe(estado: EstadoSistema | null): number | null | undefined {
  return estado === null ? undefined : (estado.ultima_vela?.timestamp ?? null)
}

export function useCiclo(llave: number | null | undefined): UsoCiclo {
  const [datos, setDatos] = useState<DatosCiclo | null>(null)
  const [fase, setFase] = useState<Fase>('cargando')
  const [mensaje, setMensaje] = useState<string | null>(null)
  const [impulso, setImpulso] = useState(0)
  const refrescar = useCallback(() => setImpulso((n) => n + 1), [])

  useEffect(() => {
    if (llave === undefined) {
      return
    }
    let vivo = true
    const peticion = new AbortController()

    void (async () => {
      const [velas, ultima, rendimiento, calidad] = await Promise.all([
        obtener('/api/candles?limit=200', esquemaVelas, { signal: peticion.signal }),
        obtener('/api/predictions/latest', esquemaPrediccionUltima, { signal: peticion.signal }),
        obtener('/api/yield', esquemaRendimiento, { signal: peticion.signal }),
        obtener('/api/model-quality', esquemaCalidadModelo, { signal: peticion.signal }),
      ])
      if (!vivo || [velas, ultima, rendimiento, calidad].some((r) => r.estado === 'cancelado')) {
        return
      }
      if (
        velas.estado === 'ok' &&
        ultima.estado === 'ok' &&
        rendimiento.estado === 'ok' &&
        calidad.estado === 'ok'
      ) {
        setDatos({
          velas: velas.datos,
          ultima: ultima.datos,
          rendimiento: rendimiento.datos,
          calidad: calidad.datos,
        })
        setFase('ok')
        setMensaje(null)
      } else {
        const fallo = [velas, ultima, rendimiento, calidad].find((r) => r.estado === 'error')
        setFase('error')
        setMensaje(fallo?.estado === 'error' ? fallo.mensaje : MENSAJE_RED)
      }
    })()

    return () => {
      vivo = false
      peticion.abort()
    }
  }, [llave, impulso])

  return { datos, fase, mensaje, refrescar }
}
