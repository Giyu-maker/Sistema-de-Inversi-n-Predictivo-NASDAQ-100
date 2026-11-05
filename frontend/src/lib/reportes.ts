/**
 * Rutas de los reportes (RF-702) — lógica pura, sin React ni red.
 *
 * La API recibe `desde`/`hasta` como `AAAA-MM-DD` **de sesión** (ET) y, si
 * no llegan, sirve las últimas 5 sesiones. Aquí solo se decide qué parámetros
 * van en la URL: una fecha vacía **no se envía**, para que el servidor
 * aplique su rango por defecto en lugar de recibir un string vacío que
 * rechazaría con 422.
 */

/** Identificador del reporte tal como lo espera `GET /api/reportes/{tipo}`. */
export type TipoReporte = 'senal' | 'rendimiento' | 'kill-switch' | 'auditoria'

/** Opciones de descarga: fechas opcionales en `AAAA-MM-DD`. */
export interface RangoReporte {
  desde?: string
  hasta?: string
}

/** Etiquetas en lenguaje llano para el selector (el texto lo elige la UI). */
export const TIPOS_REPORTE: ReadonlyArray<{ id: TipoReporte; nombre: string }> = [
  { id: 'senal', nombre: 'Señales de la IA por sesión' },
  { id: 'rendimiento', nombre: 'Rendimiento asimétrico por sesión' },
  { id: 'kill-switch', nombre: 'Eventos del Kill-Switch' },
  { id: 'auditoria', nombre: 'Resumen de auditoría' },
]

export const FORMATOS_REPORTE = ['csv', 'pdf'] as const
export type FormatoReporte = (typeof FORMATOS_REPORTE)[number]

export function esTipoReporte(valor: string): valor is TipoReporte {
  return TIPOS_REPORTE.some((t) => t.id === valor)
}

/** Aviso que acompaña al selector cuando no se pide rango explícito. */
export const AVISO_RANGO_POR_DEFECTO = 'Sin fechas: se sirven las últimas 5 sesiones.'

/**
 * URL de descarga. Lanza si el tipo no existe: una descarga a una ruta que
 * la API rechazaría con 422 es un error que conviene ver en desarrollo, no
 * un archivo vacío en la carpeta del operador.
 */
export function rutaReporte(
  tipo: TipoReporte,
  rango: RangoReporte = {},
  formato: FormatoReporte = 'csv',
): string {
  if (!esTipoReporte(tipo)) {
    throw new Error(`Tipo de reporte desconocido: ${tipo}`)
  }
  const parametros = new URLSearchParams()
  parametros.set('formato', formato)
  if (rango.desde && rango.desde.trim()) {
    parametros.set('desde', rango.desde.trim())
  }
  if (rango.hasta && rango.hasta.trim()) {
    parametros.set('hasta', rango.hasta.trim())
  }
  return `/api/reportes/${tipo}?${parametros.toString()}`
}
