/**
 * Formato es-CO y zonas IANA — el cambio de horario de EE. UU. del
 * 01/11/2026 es la prueba de fuego: hasta ese día el cierre de las 16:00 ET
 * son las 15:00 en Bogotá; después, las 16:00. Un offset fijo fallaría una
 * de las dos mitades del año.
 */
import test from 'node:test'
import assert from 'node:assert/strict'

import {
  ESPACIO_FINO,
  ZONA_COLOMBIA,
  ZONA_MERCADO,
  diaUtil,
  formatearFecha,
  formatearNumero,
  formatearPorcentaje,
  formatearRatio,
  minutosDelDia,
  posicionSesion,
} from '../src/lib/formato.ts'

// 20/10/2026 19:55:00 UTC = 14:55:00 en Bogotá (UTC-5 fijo).
const UTC_20_OCT_1955 = Date.UTC(2026, 9, 20, 19, 55, 0)

test('la fecha se escribe DD/MM/YYYY HH:mm:ss en hora de Colombia', () => {
  assert.equal(formatearFecha(UTC_20_OCT_1955), '20/10/2026 14:55:00')
})

test('ANTES del cambio de horario de EE. UU.: 16:00 ET son las 15:00 en Colombia', () => {
  // 31/10/2026 (sábado) aún está en EDT (UTC-4): 16:00 ET = 20:00 UTC.
  const cierreDeOctubre = Date.UTC(2026, 9, 31, 20, 0, 0)
  assert.equal(formatearFecha(cierreDeOctubre, ZONA_MERCADO), '31/10/2026 16:00:00')
  assert.equal(formatearFecha(cierreDeOctubre, ZONA_COLOMBIA), '31/10/2026 15:00:00')
})

test('DESPUÉS del cambio de horario de EE. UU.: 16:00 ET son las 16:00 en Colombia', () => {
  // El DST termina el 01/11/2026; el lunes 02 ya está en EST (UTC-5):
  // 16:00 ET = 21:00 UTC y la diferencia con Bogotá colapsa a cero.
  const cierreDeNoviembre = Date.UTC(2026, 10, 2, 21, 0, 0)
  assert.equal(formatearFecha(cierreDeNoviembre, ZONA_MERCADO), '02/11/2026 16:00:00')
  assert.equal(formatearFecha(cierreDeNoviembre, ZONA_COLOMBIA), '02/11/2026 16:00:00')
})

test('la confianza se formatea como porcentaje con espacio fino antes del %', () => {
  assert.equal(formatearRatio(0.2789, 1), `27,9${ESPACIO_FINO}%`)
  assert.equal(formatearRatio(0.75, 0), `75${ESPACIO_FINO}%`)
  assert.ok(formatearPorcentaje(50, 0).includes('\u202F'))
})

test('los números usan coma decimal y punto de millares (es-CO)', () => {
  assert.equal(formatearNumero(1234567.891, 2), '1.234.567,89')
  assert.equal(formatearNumero(717.32, 2), '717,32')
})

// 21/10/2026 es miércoles; 14:00 UTC = 10:00 en EDT.
const MIERCOLES_21_OCT_1000_ET = Date.UTC(2026, 9, 21, 14, 0, 0)

test('posición de sesión a mitad de la mañana', () => {
  const posicion = posicionSesion(MIERCOLES_21_OCT_1000_ET)
  assert.equal(posicion.diaUtil, true)
  assert.equal(posicion.dentro, true)
  assert.equal(posicion.minutos, 30)
  assert.ok(Math.abs(posicion.avance - 30 / 390) < 1e-9)
})

test('la apertura exacta (09:30 ET) cuenta como dentro de sesión', () => {
  const posicion = posicionSesion(Date.UTC(2026, 9, 21, 13, 30, 0))
  assert.equal(posicion.dentro, true)
  assert.equal(posicion.minutos, 0)
  assert.equal(posicion.avance, 0)
})

test('antes de abrir, después de cerrar y fin de semana', () => {
  assert.equal(posicionSesion(Date.UTC(2026, 9, 21, 12, 0, 0)).dentro, false) // 08:00 ET
  const despues = posicionSesion(Date.UTC(2026, 9, 21, 20, 30, 0)) // 16:30 ET
  assert.equal(despues.dentro, false)
  assert.equal(despues.avance, 1)
  const sabado = posicionSesion(Date.UTC(2026, 9, 24, 15, 0, 0))
  assert.equal(sabado.diaUtil, false)
  assert.equal(diaUtil(Date.UTC(2026, 9, 25, 15, 0, 0)), false) // domingo 25/10/2026
  assert.equal(sabado.dentro, false)
})

test('minutosDelDia lee la zona que se le pida', () => {
  assert.equal(minutosDelDia(Date.UTC(2026, 9, 21, 13, 30, 0), ZONA_MERCADO), 9 * 60 + 30)
  assert.equal(minutosDelDia(Date.UTC(2026, 9, 21, 13, 30, 0), ZONA_COLOMBIA), 8 * 60 + 30)
})

// -- revisión de capturas P1 (confianza, ET, sesión previa) -------------------

import { formatearConfianza, formatearHoraET, sesionPrevia } from '../src/lib/formato.ts'

test('formatearConfianza nunca devuelve «100,0 %» (P1.10)', () => {
  assert.equal(formatearConfianza(0.276), `27,6${ESPACIO_FINO}%`)
  assert.equal(formatearConfianza(0.9994), `99,9${ESPACIO_FINO}%`)
  assert.equal(formatearConfianza(0.9999), `más de 99,9${ESPACIO_FINO}%`)
  assert.equal(formatearConfianza(1), `más de 99,9${ESPACIO_FINO}%`)
})

test('formatearHoraET pinta la hora de la sesión en ET (P1.4)', () => {
  // 23/10/2026 19:55 UTC = 15:55 EDT (cierre real de la sesión del viernes).
  assert.equal(formatearHoraET(Date.UTC(2026, 9, 23, 19, 55)), '15:55 ET')
  // 04/11/2026 20:00 UTC = 15:00 EST (ya en horario de invierno).
  assert.equal(formatearHoraET(Date.UTC(2026, 10, 4, 20, 0)), '15:00 ET')
})

test('sesionPrevia distingue la última sesión (P1.5)', () => {
  const viernes = Date.UTC(2026, 9, 23, 18, 0) // viernes 23/10 · 14:00 ET
  const miercoles = Date.UTC(2026, 9, 21, 16, 0) // miércoles 21/10 · 12:00 ET
  // Misma sesión (predicción y vela del viernes): no es previa.
  assert.equal(sesionPrevia(viernes, viernes), false)
  // Predicción de una sesión anterior a la última vela: es previa.
  assert.equal(sesionPrevia(miercoles, viernes), true)
  // Sin velas no hay sesión de referencia que calificar.
  assert.equal(sesionPrevia(miercoles, null), false)
  // Las fechas se comparan en ET: 04/11/2026 (EST) ≠ 03/11/2026.
  assert.equal(
    sesionPrevia(Date.UTC(2026, 10, 3, 18, 0), Date.UTC(2026, 10, 4, 18, 0)),
    true,
  )
})
