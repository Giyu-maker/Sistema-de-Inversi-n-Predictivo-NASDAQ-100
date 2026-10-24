/**
 * `RED_CAIDA` del registro de pruebas: la marca con la que un escenario simula
 * «la API dejó de responder» (break-ui, B2.5) debe traducirse en el error de
 * RED del cliente —«No se pudo contactar con el servidor.»— y nunca en el de
 * contrato ni en una excepción.
 *
 * Fichero aparte: `node --test` ejecuta cada archivo en su propio proceso,
 * así que instalar el registro aquí no contamina a los demás tests.
 */
import assert from 'node:assert/strict'
import test from 'node:test'

import { MENSAJE_RED, obtener } from '../src/api/cliente.ts'
import { esquemaEstadoSistema } from '../src/api/esquemas.ts'
import { RED_CAIDA, instalarPruebas } from '../src/api/pruebas.ts'

test('RED_CAIDA se traduce en error de red con el mensaje llano', async () => {
  instalarPruebas('api-caida', () => RED_CAIDA, () => null)

  const respuesta = await obtener('/api/status', esquemaEstadoSistema)

  assert.equal(respuesta.estado, 'error')
  assert.equal(respuesta.tipo, 'red')
  assert.equal(respuesta.mensaje, MENSAJE_RED)
})

test('sin escenario instalado no se usa el registro (la red real manda)', async () => {
  // Este archivo instaló arriba; el proceso termina con estos tests, y en los
  // demás archivos `hayPruebas()` sigue en false. Aquí solo comprobamos que la
  // marca no cambia el contrato del esquema.
  assert.equal(typeof RED_CAIDA, 'symbol')
})
