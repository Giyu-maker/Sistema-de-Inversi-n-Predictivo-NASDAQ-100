/**
 * Escrituras del Kill-Switch — RF-501 (disparo) y RF-503 (reinicio).
 *
 * Dos funciones, dos niveles de exigencia, igual que en el backend:
 *
 * - `disparar()` **no lleva clave**: en una emergencia no se pide credencial.
 * - `reiniciar()` la lleva en la cabecera `X-Kill-Switch-Key`, que así no
 *   viaja en el cuerpo de la petición (que es lo que más suele quedar en
 *   los registros de las pasarelas).
 *
 * Ninguna lanza excepción: todo vuelve como `Respuesta<T>` y la UI traduce
 * el fallo a texto llano (mismo contrato que `cliente.ts`).
 */
import { enviar } from './cliente.ts'
import type { Respuesta } from './cliente.ts'
import { esquemaDisparo, esquemaReinicio } from './esquemas.ts'
import type { Disparo, Reinicio } from './esquemas.ts'

export const RUTA_DISPARO = '/api/kill-switch'
export const RUTA_REINICIO = '/api/kill-switch/reset'

/** Cabecera de la clave de reinicio (el backend la lee con ese alias). */
export const CABECERA_CLAVE = 'X-Kill-Switch-Key'

/** Dispara el Kill-Switch (POST sin cuerpo). */
export function disparar(signal?: AbortSignal): Promise<Respuesta<Disparo>> {
  return enviar(RUTA_DISPARO, esquemaDisparo, { signal })
}

/** Reinicia con la clave de reinicio. Una clave vacía ni siquiera se envía. */
export function reiniciar(
  clave: string,
  signal?: AbortSignal,
): Promise<Respuesta<Reinicio>> {
  const limpio = clave.trim()
  if (limpio.length === 0) {
    return Promise.resolve({
      estado: 'error',
      tipo: 'servidor',
      mensaje: 'Escriba la clave de reinicio.',
    })
  }
  return enviar(RUTA_REINICIO, esquemaReinicio, {
    signal,
    cabeceras: { [CABECERA_CLAVE]: limpio },
  })
}
