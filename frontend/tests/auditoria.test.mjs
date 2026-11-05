/**
 * Visor de auditoría — rutas y paginación (RF-701).
 *
 * Lo que la UI no puede comprobar sola: que la query string coincida con lo
 * que la API espera, que un filtro vacío no viaje de más y que la
 * paginación no se salga del total.
 */
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  AVISO_RANGO,
  LIMITE_PAGINA,
  offsetAnterior,
  offsetSiguiente,
  rutaAuditoria,
  textoPagina,
} from '../src/lib/auditoria.ts'

test('sin filtros la ruta queda limpia', () => {
  assert.equal(rutaAuditoria({}), '/api/auditoria')
  assert.equal(rutaAuditoria({ limit: 50 }), '/api/auditoria?limit=50')
})

test('los filtros viajan con los nombres que la API espera', () => {
  const ruta = rutaAuditoria({
    actor: 'SISTEMA',
    accion: 'KILL_SWITCH_TRIGGERED',
    desde: '2026-11-01',
    hasta: '2026-11-07',
    limit: 50,
    offset: 100,
  })
  const parametros = new URLSearchParams(ruta.split('?')[1])
  assert.equal(parametros.get('actor'), 'SISTEMA')
  assert.equal(parametros.get('accion'), 'KILL_SWITCH_TRIGGERED')
  assert.equal(parametros.get('desde'), '2026-11-01')
  assert.equal(parametros.get('hasta'), '2026-11-07')
  assert.equal(parametros.get('limit'), '50')
  assert.equal(parametros.get('offset'), '100')
})

test('un filtro en blanco no se envía', () => {
  const ruta = rutaAuditoria({ actor: '   ', accion: '', offset: 0 })
  assert.equal(ruta, '/api/auditoria')
})

test('offset en cero no ensucia la URL', () => {
  assert.equal(rutaAuditoria({ limit: 50, offset: 0 }), '/api/auditoria?limit=50')
})

test('la paginación no se sale del total', () => {
  assert.equal(offsetSiguiente(0, 50, 120), 50)
  assert.equal(offsetSiguiente(100, 50, 120), null, 'no hay página 3')
  assert.equal(offsetSiguiente(0, 50, 50), null, 'una sola página')
  assert.equal(offsetAnterior(50, 50), 0)
  assert.equal(offsetAnterior(0, 50), null, 'ya estás en la primera')
  assert.equal(offsetAnterior(30, 50), 0, 'nunca un offset negativo')
})

test('el texto de página dice el rango visible y el total', () => {
  assert.equal(textoPagina(0, 50, 123), '1-50 de 123')
  assert.equal(textoPagina(100, 50, 123), '101-123 de 123')
  assert.equal(textoPagina(0, 50, 0), '0 de 0')
})

test('el aviso del rango por defecto existe y es claro', () => {
  assert.match(AVISO_RANGO, /últimas 5 sesiones/)
  assert.equal(LIMITE_PAGINA, 50)
})
