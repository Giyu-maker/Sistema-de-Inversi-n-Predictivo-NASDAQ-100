/**
 * Lógica pura del gráfico de velas (B2.4): extracción de día/hora de la
 * fecha ya formateada, filtro de «última sesión» y resumen textual
 * accesible. Sin DOM ni Recharts: se corre con `node --test`.
 */
import test from 'node:test'
import assert from 'node:assert/strict'

import { diaDe, horaDe, resumirVelas, ultimaSesion } from '../src/lib/velas.ts'

function vela(fecha, open, close, low, high) {
  return { timestamp: 0, fecha, open, high, low, close, volume: 1000 }
}

test('diaDe y horaDe leen la fecha DD/MM/YYYY HH:mm:ss por posición', () => {
  assert.equal(diaDe('23/10/2026 14:55:00'), '23/10/2026')
  assert.equal(horaDe('23/10/2026 14:55:00'), '14:55')
})

test('ultimaSesion devuelve solo las velas del último día presente', () => {
  const velas = [
    vela('22/10/2026 15:55:00', 710, 711, 709, 712),
    vela('23/10/2026 09:30:00', 712, 713, 711, 714),
    vela('23/10/2026 09:35:00', 713, 712, 711, 714),
  ]
  const filtradas = ultimaSesion(velas)
  assert.equal(filtradas.length, 2)
  assert.ok(filtradas.every((v) => v.fecha.startsWith('23/10/2026')))
})

test('ultimaSesion con un solo día devuelve todo, y vacío devuelve vacío', () => {
  const una = [vela('23/10/2026 09:30:00', 1, 2, 0.5, 2.5)]
  assert.equal(ultimaSesion(una).length, 1)
  assert.deepEqual(ultimaSesion([]), [])
})

test('resumirVelas resume apertura, cierre, rango y variación', () => {
  const resumen = resumirVelas([
    vela('23/10/2026 09:30:00', 700, 702, 699, 703),
    vela('23/10/2026 09:35:00', 702, 705, 701, 706),
    vela('23/10/2026 09:40:00', 705, 704, 698, 707),
  ])
  assert.equal(resumen.apertura, 700)
  assert.equal(resumen.cierre, 704)
  assert.equal(resumen.minimo, 698)
  assert.equal(resumen.maximo, 707)
  // (704 − 700) / 700 × 100 = 0,5714…
  assert.ok(Math.abs(resumen.variacionPct - 0.5714) < 0.001)
})

test('resumirVelas devuelve null sin velas y variación null con apertura cero', () => {
  assert.equal(resumirVelas([]), null)
  const sinBase = resumirVelas([vela('23/10/2026 09:30:00', 0, 5, 0, 6)])
  assert.equal(sinBase.variacionPct, null)
})

test('formatearPorcentajeFirmado firma siempre con + o − (U+2212)', async () => {
  const { formatearPorcentajeFirmado, ESPACIO_FINO } = await import('../src/lib/formato.ts')
  assert.equal(formatearPorcentajeFirmado(0.03), `+0,03${ESPACIO_FINO}%`)
  assert.equal(formatearPorcentajeFirmado(-1.25), `−1,25${ESPACIO_FINO}%`)
  assert.equal(formatearPorcentajeFirmado(0, 2), `0,00${ESPACIO_FINO}%`)
  assert.equal(formatearPorcentajeFirmado(1.74, 0), `+2${ESPACIO_FINO}%`)
})
