/**
 * Lógica pura del control del Kill-Switch — RF-501 y RNF-001 (≤ 2 toques).
 *
 * El SRS exige que «Ejecutar Kill-Switch» esté a un máximo de 2 toques
 * desde el Dashboard: aquí se comprueba que NUNCA se dispara de un solo
 * toque y que la máquina no se puede quedar bloqueada tras un fallo.
 */
import test from 'node:test'
import assert from 'node:assert/strict'

import { etiquetaDe, ocupado, siguienteFase } from '../src/lib/killSwitch.ts'

test('el disparo necesita dos toques: pulsar y confirmar', () => {
  let fase = siguienteFase('reposo', 'pulsar')
  assert.equal(fase, 'confirmar', 'el primer toque solo pregunta')
  fase = siguienteFase(fase, 'confirmar')
  assert.equal(fase, 'enviando', 'el segundo toque ejecuta')
})

test('un solo toque jamás llega a enviar', () => {
  // Ningún evento de un solo paso, partiendo de reposo, produce 'enviando'.
  for (const evento of ['pulsar', 'confirmar', 'cancelar', 'exito', 'fallo']) {
    assert.notEqual(siguienteFase('reposo', evento), 'enviando', evento)
  }
})

test('cancelar vuelve a reposo', () => {
  assert.equal(siguienteFase('confirmar', 'cancelar'), 'reposo')
})

test('mientras se envía no se puede volver a pulsar', () => {
  assert.equal(siguienteFase('enviando', 'pulsar'), 'enviando')
  assert.equal(siguienteFase('enviando', 'confirmar'), 'enviando')
  assert.equal(ocupado('enviando'), true)
  assert.equal(ocupado('confirmar'), false)
  assert.equal(ocupado('reposo'), false)
})

test('el éxito vuelve a reposo', () => {
  assert.equal(siguienteFase('enviando', 'exito'), 'reposo')
})

test('un fallo deja el error a la vista y habilita el reintento', () => {
  // Volver a 'reposo' escondería lo que falló; 'confirmar' permite
  // reintentar con un toque.
  assert.equal(siguienteFase('enviando', 'fallo'), 'confirmar')
})

test('los eventos fuera de lugar no mueven la máquina', () => {
  assert.equal(siguienteFase('reposo', 'exito'), 'reposo')
  assert.equal(siguienteFase('reposo', 'cancelar'), 'reposo')
  assert.equal(siguienteFase('confirmar', 'pulsar'), 'confirmar')
  assert.equal(siguienteFase('confirmar', 'exito'), 'confirmar')
  assert.equal(siguienteFase('enviando', 'cancelar'), 'enviando')
})

test('la etiqueta distingue los tres estados: activo, sin verificar, ok', () => {
  assert.deepEqual(etiquetaDe({ activo: true }), {
    texto: 'Kill-Switch activo',
    clase: 'bg-rojo-fondo text-rojo-texto',
  })
  // «No lo sé» no puede verse como «todo bien».
  assert.deepEqual(etiquetaDe({ activo: null }), {
    texto: 'Estado sin verificar',
    clase: 'bg-ambar-fondo text-ambar-texto',
  })
  assert.deepEqual(etiquetaDe({ activo: false }), {
    texto: 'Kill-Switch',
    clase: 'bg-muted text-muted-foreground',
  })
  // Mientras no llegue el primer estado: tampoco se afirma nada.
  assert.deepEqual(etiquetaDe(null), etiquetaDe({ activo: null }))
})

test('el flujo completo de dos toques con un fallo y un reintento', () => {
  let fase = 'reposo'
  fase = siguienteFase(fase, 'pulsar') // 1.er toque
  fase = siguienteFase(fase, 'confirmar') // 2.º toque
  fase = siguienteFase(fase, 'fallo') // la API contestó 401/503
  assert.equal(fase, 'confirmar')
  fase = siguienteFase(fase, 'confirmar') // reintento: un toque
  assert.equal(fase, 'enviando')
  fase = siguienteFase(fase, 'exito')
  assert.equal(fase, 'reposo')
})
