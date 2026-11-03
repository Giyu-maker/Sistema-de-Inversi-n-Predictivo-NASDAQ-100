/**
 * Cliente HTTP tipado de la API de M2 — las 7 rutas del contrato RF-304.
 *
 * Tres reglas del brief de la Fase B2, todas verificables en
 * `tests/cliente.test.ts` y `tests/esquemas.test.ts`:
 *
 * 1. **Valida con Zod pero tolera campos desconocidos** (`validar`): la API
 *    usa `extra="forbid"` hacia dentro — un campo nuevo debe romper el test
 *    del backend —, pero la interfaz no se rompe si el contrato crece: lo que
 *    se lee se valida, lo demás se ignora (verificado en
 *    `tests/esquemas.test.mjs`).
 * 2. **Una respuesta que no cumple el contrato nunca llega a la UI a medias**:
 *    se descarta entera y se devuelve el estado `error` con
 *    «Respuesta inesperada del servidor».
 * 3. **Nunca lanza excepción**: todo fallo (red, timeout, HTTP, contrato) es
 *    un valor `Respuesta` que la UI traduce a texto llano.
 *
 * Cada petición tiene un timeout de 15 s (`AbortSignal.timeout`) combinado con
 * la señal del llamador (`AbortSignal.any`), de modo que desmontar un
 * componente cancela la petición en vuelo y una API colgada no encadena el
 * sondeo (RNF-003: el ciclo entero debe quedar por debajo de 500 ms; 15 s es
 * un techo de seguridad, no una expectativa).
 */
import type { ZodType } from 'zod'

import { RED_CAIDA, consultarPrueba, hayPruebas } from './pruebas.ts'

/** Texto que ve el operador cuando la respuesta no cumple el contrato. */
export const MENSAJE_CONTRATO = 'Respuesta inesperada del servidor.'
/** Texto para fallo de red o de proceso (API apagada). */
export const MENSAJE_RED = 'No se pudo contactar con el servidor.'
/** Techo por petición; en local la API responde en milisegundos. */
export const TIMEOUT_MS = 15_000

export type TipoError = 'contrato' | 'red' | 'servidor'

/**
 * Resultado de una consulta. `cancelado` no es un error para la UI: es la
 * señal de que alguien ya no necesita la respuesta (componente desmontado,
 * pestaña oculta, vela nueva que reemplaza al ciclo anterior).
 */
export type Respuesta<T> =
  | { estado: 'ok'; datos: T }
  | { estado: 'cancelado' }
  | { estado: 'error'; tipo: TipoError; mensaje: string }

/** Estado de un recurso que se refresca: para la UI, texto llano. */
export type Fase = 'cargando' | 'ok' | 'error'

/**
 * Valida un cuerpo contra el esquema. Los campos desconocidos se eliminan
 * sin fallar (comportamiento por defecto de `z.object`).
 */
export function validar<T>(cuerpo: unknown, esquema: ZodType<T>): Respuesta<T> {
  const resultado = esquema.safeParse(cuerpo)
  return resultado.success
    ? { estado: 'ok', datos: resultado.data }
    : { estado: 'error', tipo: 'contrato', mensaje: MENSAJE_CONTRATO }
}

export interface OpcionesPeticion {
  /** Señal del llamador: se combina con el timeout. */
  signal?: AbortSignal
}

/**
 * GET tipado. `ruta` incluye la query si la hay (p. ej.
 * `/api/predictions?limit=20&offset=0`).
 */
export async function obtener<T>(
  ruta: string,
  esquema: ZodType<T>,
  opciones: OpcionesPeticion = {},
): Promise<Respuesta<T>> {
  // En desarrollo, un escenario activo (`?pruebas=…`) responde sin red y
  // pasa por la MISMA validación que la API real: un cuerpo simulado
  // desviado del contrato se ve como «Respuesta inesperada». Si el escenario
  // no cubre la ruta, se contesta lo mismo — nunca se mezcla con datos
  // reales. `RED_CAIDA` simula la API apagada (break-ui: «caída a mitad de
  // sesión») y se traduce en el error de red de verdad.
  if (hayPruebas()) {
    const pruebas = consultarPrueba(ruta)
    if (pruebas === RED_CAIDA) {
      return { estado: 'error', tipo: 'red', mensaje: MENSAJE_RED }
    }
    return pruebas === undefined
      ? { estado: 'error', tipo: 'contrato', mensaje: MENSAJE_CONTRATO }
      : validar(pruebas, esquema)
  }
  if (opciones.signal?.aborted) {
    return { estado: 'cancelado' }
  }

  const timeout = AbortSignal.timeout(TIMEOUT_MS)
  const senal = opciones.signal ? AbortSignal.any([opciones.signal, timeout]) : timeout

  let respuesta: Response
  try {
    respuesta = await fetch(ruta, {
      signal: senal,
      headers: { Accept: 'application/json' },
      // Polling: nada de caché heurística del navegador.
      cache: 'no-store',
    })
  } catch {
    if (opciones.signal?.aborted) {
      return { estado: 'cancelado' }
    }
    return {
      estado: 'error',
      tipo: 'red',
      mensaje: timeout.aborted ? 'El servidor no respondió a tiempo.' : MENSAJE_RED,
    }
  }

  // Errores y contratos se traducen igual que en GET (arriba).
  return aRespuesta<T>(respuesta, esquema)
}

/**
 * Respuesta HTTP → `Respuesta<T>`: el mensaje de error de la API si la hay
 * (ya viene en español, `src/endpoints/errores.py`), o la validación del
 * cuerpo. Compartida por GET y POST para que las dos rutas traduzcan un
 * fallo exactamente igual.
 */
async function aRespuesta<T>(
  respuesta: Response,
  esquema: ZodType<T>,
): Promise<Respuesta<T>> {
  if (!respuesta.ok) {
    const cuerpo: unknown = await respuesta.json().catch(() => null)
    const mensaje = (cuerpo as { mensaje?: unknown } | null)?.mensaje
    return {
      estado: 'error',
      tipo: 'servidor',
      mensaje:
        typeof mensaje === 'string' && mensaje.length > 0
          ? mensaje
          : `El servidor respondió con un error (código ${respuesta.status}).`,
    }
  }

  const cuerpo: unknown = await respuesta.json().catch(() => null)
  // JSON inexistente o inválido → `validar` falla → estado de contrato.
  return validar(cuerpo, esquema)
}

export interface OpcionesEnvio {
  /** Señal del llamador: se combina con el timeout. */
  signal?: AbortSignal
  /** Cuerpo JSON. Si no se envía, la petición va sin cuerpo. */
  cuerpo?: unknown
  /** Cabeceras extra (p. ej. la clave de reinicio del Kill-Switch). */
  cabeceras?: Record<string, string>
}

/**
 * POST tipado. Mismas garantías que `obtener`: timeout, sin excepción,
 * validación con Zod y mensajes de error en español.
 *
 * **No simula escrituras en los escenarios de desarrollo** (`?pruebas=…`):
 * esos escenarios replican cuerpos de lectura, y fingir un disparo del
 * Kill-Switch sería engañar al que está probando la pantalla.
 */
export async function enviar<T>(
  ruta: string,
  esquema: ZodType<T>,
  opciones: OpcionesEnvio = {},
): Promise<Respuesta<T>> {
  if (hayPruebas()) {
    return {
      estado: 'error',
      tipo: 'servidor',
      mensaje:
        'Los escenarios de desarrollo no simulan escrituras: pruebe contra el servidor real.',
    }
  }
  if (opciones.signal?.aborted) {
    return { estado: 'cancelado' }
  }

  const timeout = AbortSignal.timeout(TIMEOUT_MS)
  const senal = opciones.signal ? AbortSignal.any([opciones.signal, timeout]) : timeout

  let respuesta: Response
  try {
    respuesta = await fetch(ruta, {
      method: 'POST',
      signal: senal,
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        ...(opciones.cabeceras ?? {}),
      },
      cache: 'no-store',
      body: opciones.cuerpo === undefined ? undefined : JSON.stringify(opciones.cuerpo),
    })
  } catch {
    if (opciones.signal?.aborted) {
      return { estado: 'cancelado' }
    }
    return {
      estado: 'error',
      tipo: 'red',
      mensaje: timeout.aborted ? 'El servidor no respondió a tiempo.' : MENSAJE_RED,
    }
  }

  return aRespuesta<T>(respuesta, esquema)
}
