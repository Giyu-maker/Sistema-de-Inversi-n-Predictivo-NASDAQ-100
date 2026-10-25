/**
 * Espejo del contrato: los 7 cuerpos capturados de la API de B1 (viva, con
 * el M1 sirviendo) deben pasar la validación Zod, y los 4 escenarios de
 * desarrollo deben pasarla también en todas sus rutas — un fixture desviado
 * del contrato tiene que tronar aquí, no romper la UI.
 */
import test from 'node:test'
import assert from 'node:assert/strict'

import { MENSAJE_CONTRATO, validar } from '../src/api/cliente.ts'
import {
  esquemaCalidadModelo,
  esquemaEstadoSistema,
  esquemaPrediccionPagina,
  esquemaPrediccionUltima,
  esquemaRendimiento,
  esquemaSalud,
  esquemaVelas,
} from '../src/api/esquemas.ts'
import { ESCENARIOS, esEscenario, servirEscenario } from '../src/fixtures/escenarios.ts'

// -- cuerpos reales capturados de `127.0.0.1:8010` --------------------------

const ESTADO_REAL = {
  estado: 'CERRADO',
  motivo: 'Mercado cerrado: Después del cierre (09:30-16:00 ET).',
  mercado: {
    abierto: false,
    motivo: 'Después del cierre (09:30-16:00 ET)',
    feriado: false,
    zona: 'America/New_York',
    horario: '09:30-16:00 ET',
  },
  ultima_vela: { timestamp: 1792526100000, fecha: '20/10/2026 14:55:00', precio: 717.32, antiguedad_s: 11913 },
  retraso_max_s: 600,
  ia: { disponible: true, detalle: 'La IA está activa y el modelo cargado.' },
  modo: {
    nombre: 'ASESOR',
    descripcion: 'La IA solo recomienda; un operador humano ejecuta cada orden manualmente.',
    autonomo_disponible: false,
    motivo_bloqueo: 'Requiere autorización explícita de la Gerente General y autenticación multifactor local (OTP).',
  },
  perfil: { nombre: 'MODERATE', umbral_confianza: 0.75, max_loss_day: 1.25, por_defecto: false },
  ultima_prediccion: {
    id: '561b6e22-54d1-492e-ac67-bfd9b6a68152',
    prediction_time: 1792526100000,
    fecha: '20/10/2026 14:55:00',
    trend_prediction: 'UP',
    confidence_score: 0.2789,
    technical_rationale: 'Atribución de la decisión (gradiente × entrada) sobre una ventana de 12 velas. Probabilidades de clase: DOWN 25,9 % / STABLE 46,2 % / UP 27,9 %. Decisión: UP con 27,9 % de confianza.',
    resultado_real: {
      estado: 'PENDIENTE',
      motivo: 'Horizonte de 15 min aún no completo: la predicción está entre las 3 últimas velas de la serie.',
      retorno_pct: null,
      etiqueta: null,
      banda_pct: null,
    },
  },
  senal: {
    operable: false,
    motivo: 'Confianza 0,28 por debajo del umbral 0,75 del perfil MODERATE.',
    umbral: 0.75,
    perfil: 'MODERATE',
    tendencia: 'UP',
    confianza: 0.2789,
  },
  avisos: [],
}

const VELAS_REAL = {
  velas: [
    { timestamp: 1792525500000, fecha: '20/10/2026 14:45:00', open: 717.62, high: 717.81, low: 717.45, close: 717.54, volume: 6747 },
    { timestamp: 1792525800000, fecha: '20/10/2026 14:50:00', open: 717.51, high: 717.675, low: 716.93, close: 717.595, volume: 8256 },
    { timestamp: 1792526100000, fecha: '20/10/2026 14:55:00', open: 717.57, high: 717.57, low: 716.855, close: 717.32, volume: 55204 },
  ],
  total: 3,
  aggregate: '5m',
  nota: 'Velas de 5 minutos de QQQ, el índice que sigue el sistema. Las agregaciones superiores se guardan en la base de precios.',
}

const ULTIMA_REAL = {
  encontrada: true,
  prediccion: ESTADO_REAL.ultima_prediccion,
  senal: ESTADO_REAL.senal,
  avisos: [],
}

const PAGINA_REAL = {
  predicciones: [ESTADO_REAL.ultima_prediccion],
  total: 1,
  limit: 2,
  offset: 0,
  avisos: [],
}

const RENDIMIENTO_REAL = {
  virtual: true,
  nota: 'Rendimiento virtual calculado sobre los precios históricos: sin capital en riesgo y sin órdenes enviadas (Modo Asesor).',
  disponible: true,
  motivo: null,
  apertura_sesion: { timestamp: 1792503000000, fecha: '20/10/2026 08:30:00', precio: 715.3, antiguedad_s: null },
  ultima_vela: { timestamp: 1792526100000, fecha: '20/10/2026 14:55:00', precio: 717.32, antiguedad_s: 11913 },
  variacion_nasdaq_pct: 0.2823989934293437,
  rendimiento_ritech_pct: 0.5647979868586874,
  limite_perdida_pct: 1.25,
  umbral_perdida_alcanzado: false,
}

const CALIDAD_REAL = {
  disponible: true,
  motivo: null,
  model_type: 'mlp',
  feature_schema_version: '1',
  entrenado_en_ms: 1792289791093,
  entrenado_en: '17/10/2026 21:16:31',
  dispositivo_entrenamiento: 'cpu',
  macro_f1_test: 0.4312686359675911,
  banda_stable_pct: 0.1,
  acierto_direccional: {
    ratio: 0.4728888888888889,
    n: 1125,
    aciertos: 532,
    ic_95: [0.43872988269858076, 0.5078403054662898],
    fuente: 'informe_entrenamiento',
    ventaja_demostrada: false,
  },
  nota: 'Modelo local sin ventaja direccional demostrada: la interfaz no emite órdenes de compra ni de venta, solo tendencia con su nivel de confianza.',
}

const SALUD_REAL = { estado: 'ok', servicio: 'api', version: '0.1.0' }

// -- el contrato --------------------------------------------------------------

test('los 7 cuerpos de la API real cumplen el espejo Zod', () => {
  assert.equal(validar(ESTADO_REAL, esquemaEstadoSistema).estado, 'ok')
  assert.equal(validar(VELAS_REAL, esquemaVelas).estado, 'ok')
  assert.equal(validar(ULTIMA_REAL, esquemaPrediccionUltima).estado, 'ok')
  assert.equal(validar(PAGINA_REAL, esquemaPrediccionPagina).estado, 'ok')
  assert.equal(validar(RENDIMIENTO_REAL, esquemaRendimiento).estado, 'ok')
  assert.equal(validar(CALIDAD_REAL, esquemaCalidadModelo).estado, 'ok')
  assert.equal(validar(SALUD_REAL, esquemaSalud).estado, 'ok')
})

test('tolera campos desconocidos (el contrato puede crecer) y los elimina', () => {
  const conExtra = { ...ESTADO_REAL, campo_nuevo_del_contrato: 42 }
  const respuesta = validar(conExtra, esquemaEstadoSistema)
  assert.equal(respuesta.estado, 'ok')
  assert.ok(!('campo_nuevo_del_contrato' in respuesta.datos))
})

test('una respuesta que incumple el contrato se descarta entera, sin crash', () => {
  const roto = {
    ...ESTADO_REAL,
    ultima_vela: { ...ESTADO_REAL.ultima_vela, precio: 'caro' },
  }
  assert.deepEqual(validar(roto, esquemaEstadoSistema), {
    estado: 'error',
    tipo: 'contrato',
    mensaje: MENSAJE_CONTRATO,
  })
})

test('un campo obligatorio ausente también es incumplimiento', () => {
  const sinMotivo = { ...ESTADO_REAL }
  delete sinMotivo.motivo
  assert.equal(validar(sinMotivo, esquemaEstadoSistema).estado, 'error')
})

// -- los escenarios de desarrollo --------------------------------------------

const RUTAS = [
  ['/api/status', esquemaEstadoSistema],
  ['/api/candles?limit=200', esquemaVelas],
  ['/api/predictions/latest', esquemaPrediccionUltima],
  ['/api/predictions?limit=20&offset=0', esquemaPrediccionPagina],
  ['/api/yield', esquemaRendimiento],
  ['/api/model-quality', esquemaCalidadModelo],
  ['/api/health', esquemaSalud],
]

test('todos los escenarios cumplen el contrato en todas sus rutas', () => {
  for (const escenario of ESCENARIOS) {
    for (const [ruta, esquema] of RUTAS) {
      const cuerpo = servirEscenario(escenario, ruta, Date.now())
      assert.equal(validar(cuerpo, esquema).estado, 'ok', `${escenario} → ${ruta}`)
    }
  }
})

test('el paginado del historial largo respeta limit/offset y el total', () => {
  const ahora = Date.now()
  const primera = servirEscenario('historial-largo', '/api/predictions?limit=20&offset=0', ahora)
  const segunda = servirEscenario('historial-largo', '/api/predictions?limit=20&offset=20', ahora)
  assert.equal(primera.predicciones.length, 20)
  assert.equal(primera.total, 180)
  assert.equal(segunda.offset, 20)
  assert.notEqual(primera.predicciones[0].id, segunda.predicciones[0].id)
  assert.ok(primera.predicciones[0].prediction_time > segunda.predicciones[0].prediction_time)
})

test('la más reciente del historial está PENDIENTE; en el fondo, resueltas', () => {
  const ahora = Date.now()
  const pagina = servirEscenario('historial-largo', '/api/predictions?limit=20&offset=0', ahora)
  assert.equal(pagina.predicciones[0].resultado_real.estado, 'PENDIENTE')
  const profunda = servirEscenario('historial-largo', '/api/predictions?limit=20&offset=80', ahora)
  const estados = new Set(profunda.predicciones.map((fila) => fila.resultado_real.estado))
  assert.ok(estados.has('DISPONIBLE'), 'el fondo del historial debe traer resultados resueltos')
  assert.ok(!estados.has('PENDIENTE'), 'PENDIENTE solo cabe en las 3 velas más recientes')
})

test('las velas del escenario se limitan como la API real', () => {
  const cuerpo = servirEscenario('historial-largo', '/api/candles?limit=5', Date.now())
  assert.equal(cuerpo.velas.length, 5)
  assert.equal(cuerpo.velas[0].timestamp < cuerpo.velas[4].timestamp, true)
  assert.equal(cuerpo.aggregate, '5m')
})

test('senal-operable: señal operable de verdad y estado AMARILLO (UP ≠ STABLE)', () => {
  const status = servirEscenario('senal-operable', '/api/status', Date.now())
  assert.equal(status.estado, 'AMARILLO')
  assert.equal(status.senal.operable, true)
  assert.ok(status.senal.motivo.startsWith('Señal operable: tendencia al alza'))
})

test('sin-predicciones: verde, sin señal y con el copy literal del backend', () => {
  const status = servirEscenario('sin-predicciones', '/api/status', Date.now())
  assert.equal(status.estado, 'VERDE')
  assert.equal(status.ultima_prediccion, null)
  assert.equal(status.senal.operable, false)
  assert.equal(
    status.senal.motivo,
    'Sin predicción registrada: no hay señal que evaluar. Umbral del perfil Moderado: 75 %.',
  )
  const ultima = servirEscenario('sin-predicciones', '/api/predictions/latest', Date.now())
  assert.equal(ultima.encontrada, false)
  assert.equal(ultima.prediccion, null)
})

test('esEscenario distingue los nombres válidos', () => {
  assert.equal(esEscenario('senal-operable'), true)
  assert.equal(esEscenario('no-existe'), false)
  assert.equal(esEscenario(null), false)
})

test('una ruta fuera del escenario no se responde: nunca se mezcla con la red', () => {
  assert.equal(servirEscenario('senal-operable', '/api/lo-que-sea', Date.now()), undefined)
})
