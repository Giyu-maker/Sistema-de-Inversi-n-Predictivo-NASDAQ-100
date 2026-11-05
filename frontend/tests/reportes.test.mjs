/**
 * Rutas de descarga de reportes (RF-702) — lógica pura del selector.
 *
 * Lo que se prueba aquí es lo que la UI no puede comprobar sola: que la URL
 * que va en el enlace sea exactamente la que la API espera (fechas en
 * `AAAA-MM-DD`, sin parámetros vacíos y con el formato correcto).
 */
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  AVISO_RANGO_POR_DEFECTO,
  FORMATOS_REPORTE,
  TIPOS_REPORTE,
  esTipoReporte,
  rutaReporte,
} from '../src/lib/reportes.ts'

test('los cuatro tipos del backend existen en el selector', () => {
  assert.deepEqual(
    TIPOS_REPORTE.map((t) => t.id),
    ['senal', 'rendimiento', 'kill-switch', 'auditoria'],
  )
  for (const tipo of TIPOS_REPORTE) {
    assert.equal(esTipoReporte(tipo.id), true)
    assert.ok(tipo.nombre.length > 0, 'cada tipo tiene etiqueta en lenguaje llano')
  }
  assert.equal(esTipoReporte('ordenes'), false)
})

test('la URL lleva formato y las dos fechas cuando hay rango', () => {
  const ruta = rutaReporte('senal', { desde: '2026-11-03', hasta: '2026-11-07' }, 'csv')
  assert.equal(
    ruta,
    '/api/reportes/senal?formato=csv&desde=2026-11-03&hasta=2026-11-07',
  )
})

test('sin fechas no se envía ningún parámetro de rango', () => {
  // Un string vacío llegaría como `desde=` y el servidor respondería 422:
  // lo correcto es omitirlo y que aplique su rango por defecto.
  const ruta = rutaReporte('auditoria', {}, 'pdf')
  assert.equal(ruta, '/api/reportes/auditoria?formato=pdf')
  assert.equal(ruta.includes('desde='), false)
  assert.equal(ruta.includes('hasta='), false)
})

test('una fecha sola define un rango de un día', () => {
  assert.equal(
    rutaReporte('rendimiento', { hasta: '2026-11-05' }, 'csv'),
    '/api/reportes/rendimiento?formato=csv&hasta=2026-11-05',
  )
  assert.equal(
    rutaReporte('rendimiento', { desde: '2026-11-05' }, 'csv'),
    '/api/reportes/rendimiento?formato=csv&desde=2026-11-05',
  )
})

test('las fechas se recortan: un espacio no arruina la ruta', () => {
  assert.equal(
    rutaReporte('kill-switch', { desde: ' 2026-11-03 ' }, 'csv'),
    '/api/reportes/kill-switch?formato=csv&desde=2026-11-03',
  )
})

test('un tipo desconocido truena en lugar de fabricar una URL muerta', () => {
  // Sin cast: el archivo es .mjs, y el runtime es el que tiene que rechazar
  // un tipo que la API no acepta.
  assert.throws(() => rutaReporte('ordenes', {}, 'csv'), /desconocido/)
})

test('el aviso del rango por defecto dice las cinco sesiones', () => {
  assert.match(AVISO_RANGO_POR_DEFECTO, /5 sesiones/)
  assert.deepEqual([...FORMATOS_REPORTE], ['csv', 'pdf'])
})
