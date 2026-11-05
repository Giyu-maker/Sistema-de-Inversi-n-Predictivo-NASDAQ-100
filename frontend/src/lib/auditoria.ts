/**
 * Visor de auditoría: rutas y paginación, sin React ni red (RF-701).
 *
 * Los filtros van en la query string con los mismos nombres que la API
 * (`actor`, `accion`, `desde`, `hasta`, `limit`, `offset`) y un filtro
 * vacío **no se envía**, igual que en los reportes: la API aplica su
 * rango por defecto en lugar de recibir un string que ignoraría.
 */

export interface FiltroAuditoria {
  actor?: string
  accion?: string
  desde?: string
  hasta?: string
  limit?: number
  offset?: number
}

/** Tamaño de página que ofrece la API (Query ge=1, le=200). */
export const LIMITE_PAGINA = 50

/** Aviso del rango por defecto, en palabras del operador. */
export const AVISO_RANGO = 'Sin fechas: se consultan las últimas 5 sesiones.'

export function rutaAuditoria(filtro: FiltroAuditoria = {}): string {
  const parametros = new URLSearchParams()
  for (const [clave, valor] of [
    ['actor', filtro.actor],
    ['accion', filtro.accion],
    ['desde', filtro.desde],
    ['hasta', filtro.hasta],
  ] as const) {
    if (valor && valor.trim()) {
      parametros.set(clave, valor.trim())
    }
  }
  if (filtro.limit) {
    parametros.set('limit', String(filtro.limit))
  }
  if (filtro.offset) {
    parametros.set('offset', String(filtro.offset))
  }
  const consulta = parametros.toString()
  return consulta ? `/api/auditoria?${consulta}` : '/api/auditoria'
}

/** Offset de la página siguiente, o `null` si no hay más. */
export function offsetSiguiente(offset: number, limit: number, total: number): number | null {
  const siguiente = offset + limit
  return siguiente < total ? siguiente : null
}

/** Offset de la página anterior, o `null` si ya estás en la primera. */
export function offsetAnterior(offset: number, limit: number): number | null {
  return offset > 0 ? Math.max(0, offset - limit) : null
}

/** «1-50 de 123» — lo que ve el operador bajo la tabla. */
export function textoPagina(offset: number, limit: number, total: number): string {
  if (total === 0) {
    return '0 de 0'
  }
  const primero = offset + 1
  const ultimo = Math.min(offset + limit, total)
  return `${primero}-${ultimo} de ${total}`
}
