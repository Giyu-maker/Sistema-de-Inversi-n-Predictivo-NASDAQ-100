/**
 * El latido: `/api/status` cada 10 s (decisión D8).
 *
 * Reglas del brief de la Fase B2, todas acá:
 *
 * - **Sondeo dirigido por temporizador encadenado**, nunca `setInterval`: la
 *   siguiente petición se programa cuando la anterior termina, de modo que
 *   nunca se solapan.
 * - **Pestaña oculta → se pausa** (`visibilitychange`); al volver se pide de
 *   inmediato. Cancelar en vuelo al desmontar y al ocultar.
 * - **Backoff ante errores**: `base × 2^fallos` con tope de 60 s; el primer
 *   acierto vuelve al intervalo de 10 s.
 * - Un error no borra el último estado bueno: la UI conserva el dato y muestra
 *   el mensaje del problema («No se pudo contactar con el servidor.»).
 */
import { useCallback, useEffect, useState } from 'react'

import { obtener } from '../api/cliente.ts'
import type { Fase } from '../api/cliente.ts'
import { esquemaEstadoSistema } from '../api/esquemas.ts'
import type { EstadoSistema } from '../api/esquemas.ts'

export const INTERVALO_BASE_MS = 10_000
export const INTERVALO_MAXIMO_MS = 60_000

export interface UsoEstado {
  /** Último estado bueno recibido (o `null` si aún no llega ninguno). */
  estado: EstadoSistema | null
  fase: Fase
  mensaje: string | null
  /** Pide ahora mismo (reinicia el temporizador y el backoff). */
  refrescar: () => void
}

export function useEstado(): UsoEstado {
  const [estado, setEstado] = useState<EstadoSistema | null>(null)
  const [fase, setFase] = useState<Fase>('cargando')
  const [mensaje, setMensaje] = useState<string | null>(null)
  const [impulso, setImpulso] = useState(0)
  const refrescar = useCallback(() => setImpulso((n) => n + 1), [])

  useEffect(() => {
    let vivo = true
    let fallos = 0
    let temporizador: ReturnType<typeof setTimeout> | undefined
    let peticion: AbortController | null = null

    function programar(ms: number): void {
      window.clearTimeout(temporizador)
      if (vivo) {
        temporizador = setTimeout(() => void pedir(), ms)
      }
    }

    async function pedir(): Promise<void> {
      if (!vivo || document.hidden) {
        return
      }
      peticion?.abort()
      peticion = new AbortController()
      const respuesta = await obtener('/api/status', esquemaEstadoSistema, {
        signal: peticion.signal,
      })
      if (!vivo || respuesta.estado === 'cancelado') {
        return
      }
      if (respuesta.estado === 'ok') {
        fallos = 0
        setEstado(respuesta.datos)
        setFase('ok')
        setMensaje(null)
        programar(INTERVALO_BASE_MS)
      } else {
        fallos += 1
        setFase('error')
        setMensaje(respuesta.mensaje)
        programar(Math.min(INTERVALO_BASE_MS * 2 ** fallos, INTERVALO_MAXIMO_MS))
      }
    }

    function alCambiarVisibilidad(): void {
      if (document.hidden) {
        window.clearTimeout(temporizador)
        peticion?.abort()
      } else if (vivo) {
        fallos = 0
        void pedir()
      }
    }

    document.addEventListener('visibilitychange', alCambiarVisibilidad)
    void pedir()

    return () => {
      vivo = false
      window.clearTimeout(temporizador)
      peticion?.abort()
      document.removeEventListener('visibilitychange', alCambiarVisibilidad)
    }
  }, [impulso])

  return { estado, fase, mensaje, refrescar }
}
