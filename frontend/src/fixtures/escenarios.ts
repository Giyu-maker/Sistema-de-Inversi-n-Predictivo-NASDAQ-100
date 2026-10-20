/**
 * Escenarios de prueba — SOLO desarrollo, nunca en el bundle de producción.
 *
 * `main.tsx` importa este módulo dinámicamente y solo detrás de
 * `import.meta.env.DEV`; `vite build` elimina la rama entera
 * (`tests/dist-sin-fixtures.test.mjs` lo verifica con grep sobre `dist/`).
 *
 * Reglas del brief de la Fase B2:
 *
 * 1. **Solo estados que el sistema real no produce a demanda** (señal
 *    operable, historial largo, ventana sin predicciones, extremos). Todo lo
 *    demás — incluido el copy — es lo que la API de B1 devuelve de verdad:
 *    los textos de señal replican `src/security/perfiles.py`, los de estado
 *    `src/endpoints/estado.py`, los de resultado `src/utils/resultados.py` y
 *    las notas de vértice `velas.py` / `rendimiento`.
 * 2. **Tipados contra el contrato**: cada escenario se construye con los
 *    tipos inferidos de `src/api/esquemas.ts` y `obtener()` los vuelve a
 *    validar con Zod antes de que lleguen a la UI.
 * 3. **Vivos respecto al reloj**: cada consulta se construye con `Date.now()`
 *    de modo que la vela avanza cada 5 minutos y el latido de 10 s percibe el
 *    cambio igual que con datos reales. Las fechas se generan con
 *    `formatearFecha` (zona Colombia), igual que `src/utils/fechas.py`.
 *
 * Un escenario finge la sesión que describe aunque el reloj real esté fuera
 * de ella («senal-operable» a las 22:00 ET sigue diciendo mercado abierto):
 * sirve para ver estados que de otro modo solo existen en horario de mercado.
 */
import type {
  CalidadModelo,
  EstadoSistema,
  Prediccion,
  PrediccionPagina,
  PrediccionUltima,
  Rendimiento,
  Salud,
  Senal,
  Velas,
  Vela,
} from '../api/esquemas.ts'
import type { ResolutorPruebas } from '../api/pruebas.ts'
import {
  APERTURA_MINUTOS,
  CIERRE_MINUTOS,
  ZONA_MERCADO,
  diaUtil,
  formatearFecha,
  minutosDelDia,
} from '../lib/formato.ts'

export const ESCENARIOS = [
  'senal-operable',
  'historial-largo',
  'sin-predicciones',
  'extremos',
] as const

export type NombreEscenario = (typeof ESCENARIOS)[number]

export function esEscenario(valor: unknown): valor is NombreEscenario {
  return (
    typeof valor === 'string' && (ESCENARIOS as readonly string[]).includes(valor)
  )
}

// -- constantes: copy literal del backend ------------------------------------

const VELA_MS = 5 * 60 * 1000
const HORIZONTE_VELAS = 3
const BANDA_PCT = 0.1
const PERFIL = 'MODERATE'
const UMBRAL = 0.75

const NOTA_VELAS =
  'Velas de 5 minutos del índice (proxy QQQ, RF-101). ' +
  'Las agregaciones superiores se calculan en InfluxDB.'
const NOTA_RENDIMIENTO =
  'Rendimiento virtual calculado sobre la serie histórica: sin capital en ' +
  'riesgo y sin órdenes enviadas (Modo Asesor, RF-401).'
const NOTA_CALIDAD =
  'Modelo local sin ventaja direccional demostrada: la interfaz no emite ' +
  'órdenes de compra ni de venta, solo tendencia con su nivel de confianza.'
const DESCRIPCION_ASESOR =
  'La IA solo recomienda; un operador humano ejecuta manualmente (RF-401).'
const MOTIVO_BLOQUEO =
  'Requiere autorización explícita de la Gerente General y autenticación OTP ' +
  'local (RF-401, Sprint 4).'
const IA_OK = 'Servicio de inferencia activo y modelo cargado.'
const HORARIO = '09:30-16:00 ET'
const MOTIVO_ABIERTO = 'Horario regular de mercado'
const MOTIVO_CIERRADO = 'Después del cierre (09:30-16:00 ET)'
const MOTIVO_VERDE = 'Dato actualizado, IA disponible y última predicción STABLE.'
const MOTIVO_CIERRA = 'Mercado cerrado: Después del cierre (09:30-16:00 ET).'
const MOTIVO_ULTIMA_UP = 'La última predicción es UP, no STABLE.'

const MOTIVO_PENDIENTE =
  'Horizonte de 15 min aún no completo: la predicción está entre las 3 ' +
  'últimas velas de la serie.'
const MOTIVO_DISPONIBLE = 'Horizonte de 15 min completo dentro de la sesión.'
const MOTIVO_NO_APLICA =
  'El horizonte de 15 min cae fuera de la sesión (cierre de mercado).'

// -- piezas comunes ----------------------------------------------------------

function redondear(valor: number, decimales: number): number {
  const factor = 10 ** decimales
  return Math.round(valor * factor) / factor
}

/** Mismo criterio que `_numero()` de `src/security/perfiles.py`. */
function dos(valor: number): string {
  return valor.toFixed(2).replace('.', ',')
}

// -- señales: réplicas de `src/security/perfiles.py` -------------------------

/** Copia literal de `UMBRALES_POR_PERFIL` (RF-403). */
const UMBRALES: Record<string, number> = {
  CONVENTIONAL: 0.85,
  MODERATE: 0.75,
  AGGRESSIVE: 0.65,
}

function senalOperable(tendencia: string, confianza: number, perfil: string): Senal {
  const umbral = UMBRALES[perfil]
  return {
    operable: true,
    motivo: `Señal operable: ${tendencia} con confianza ${dos(confianza)} ≥ umbral ${dos(umbral)} del perfil ${perfil}.`,
    umbral,
    perfil,
    tendencia,
    confianza,
  }
}

function senalBaja(tendencia: string, confianza: number, perfil: string): Senal {
  const umbral = UMBRALES[perfil]
  return {
    operable: false,
    motivo: `Confianza ${dos(confianza)} por debajo del umbral ${dos(umbral)} del perfil ${perfil}.`,
    umbral,
    perfil,
    tendencia,
    confianza,
  }
}

function senalStable(confianza: number, perfil: string): Senal {
  return {
    operable: false,
    motivo:
      'STABLE nunca es una señal operable: el modelo no anticipa movimiento ' +
      'aprovechable, con cualquier nivel de confianza.',
    umbral: UMBRALES[perfil],
    perfil,
    tendencia: 'STABLE',
    confianza,
  }
}

function senalSinPrediccion(perfil: string): Senal {
  return {
    operable: false,
    motivo:
      'Sin predicción registrada: no hay señal que evaluar. ' +
      `Umbral del perfil ${perfil}: ${dos(UMBRALES[perfil])}.`,
    umbral: UMBRALES[perfil],
    perfil,
    tendencia: null,
    confianza: null,
  }
}

function senalDe(tendencia: string, confianza: number, perfil: string): Senal {
  if (tendencia === 'STABLE') return senalStable(confianza, perfil)
  return confianza >= UMBRALES[perfil]
    ? senalOperable(tendencia, confianza, perfil)
    : senalBaja(tendencia, confianza, perfil)
}

// -- resultado real: réplicas de `src/utils/resultados.py` -------------------

function pendiente() {
  return {
    estado: 'PENDIENTE',
    motivo: MOTIVO_PENDIENTE,
    retorno_pct: null,
    etiqueta: null,
    banda_pct: null,
  }
}

function noAplica() {
  return {
    estado: 'NO_APLICA',
    motivo: MOTIVO_NO_APLICA,
    retorno_pct: null,
    etiqueta: null,
    banda_pct: null,
  }
}

function disponible(retornoPct: number) {
  const etiqueta = retornoPct > BANDA_PCT ? 'UP' : retornoPct < -BANDA_PCT ? 'DOWN' : 'STABLE'
  return {
    estado: 'DISPONIBLE',
    motivo: MOTIVO_DISPONIBLE,
    retorno_pct: redondear(retornoPct, 4),
    etiqueta,
    banda_pct: BANDA_PCT,
  }
}

// -- velas y slots de sesión -------------------------------------------------

function construirVelas(
  desdeMs: number,
  cantidad: number,
  base: number,
  factorVolumen = 1,
): Vela[] {
  const velas: Vela[] = []
  let anterior = base
  for (let i = 0; i < cantidad; i += 1) {
    const timestamp = desdeMs + i * VELA_MS
    const movimiento = Math.sin(i * 0.7) * 0.42 + Math.sin(i * 0.11) * 0.85
    const open = redondear(anterior, 2)
    const close = redondear(anterior + movimiento, 2)
    velas.push({
      timestamp,
      fecha: formatearFecha(timestamp),
      open,
      high: redondear(Math.max(open, close) + 0.24, 2),
      low: redondear(Math.min(open, close) - 0.21, 2),
      close,
      volume: Math.round((4200 + Math.abs(movimiento) * 24000) * factorVolumen),
    })
    anterior = close
  }
  return velas
}

function dentroDeSesion(ms: number): boolean {
  const delDia = minutosDelDia(ms, ZONA_MERCADO)
  return diaUtil(ms, ZONA_MERCADO) && delDia >= APERTURA_MINUTOS && delDia < CIERRE_MINUTOS
}

/** Último slot de 5 min del día hábil anterior a `desde`. */
function finDeSesionAnterior(desde: number): number {
  let cursor = desde - VELA_MS
  for (let guardia = 0; guardia < 5000; guardia += 1) {
    if (diaUtil(cursor, ZONA_MERCADO) && minutosDelDia(cursor, ZONA_MERCADO) === CIERRE_MINUTOS - 5) {
      return cursor
    }
    cursor -= VELA_MS
  }
  return desde
}

/**
 * Los últimos `cantidad` slots de sesión regular, de más reciente a más
 * antiguo, saltando noches y fines de semana como haría el sistema real.
 */
function slotsDeSesion(hasta: number, cantidad: number): number[] {
  const tramos: number[] = []
  let cursor = Math.floor(hasta / VELA_MS) * VELA_MS
  for (let guardia = 0; tramos.length < cantidad && guardia < 60_000; guardia += 1) {
    if (dentroDeSesion(cursor)) {
      tramos.push(cursor)
    }
    cursor = dentroDeSesion(cursor) ? cursor - VELA_MS : finDeSesionAnterior(cursor)
  }
  return tramos
}

// -- predicciones ------------------------------------------------------------

interface Semilla {
  tendencia: string
  confianza: number
  razonamiento: string
}

function aPrediccion(
  slotMs: number,
  semilla: Semilla,
  resultado: ReturnType<typeof disponible> | ReturnType<typeof pendiente> | ReturnType<typeof noAplica>,
): Prediccion {
  return {
    id: crypto.randomUUID(),
    prediction_time: slotMs,
    fecha: formatearFecha(slotMs),
    trend_prediction: semilla.tendencia,
    confidence_score: semilla.confianza,
    technical_rationale: semilla.razonamiento,
    resultado_real: resultado,
  }
}

/**
 * Historial descendente por sesiones (`sesiones[0]` es la más reciente y
 * cada sesión va de más reciente a más antiguo). Los slots del FINAL de cada
 * sesión son los índices bajos: sus 3 primeras posiciones son las 3 velas
 * previas al cierre — PENDIENTE en la última sesión y NO_APLICA en las
 * anteriores, como en `src/utils/resultados.py`.
 */
function construirHistorial(sesiones: number[][], semillas: Semilla[]): Prediccion[] {
  const filas: Prediccion[] = []
  sesiones.forEach((sesion, indiceSesion) => {
    const esUltima = indiceSesion === 0
    sesion.forEach((slot, i) => {
      const enElCierre = i < HORIZONTE_VELAS
      const resultado = enElCierre
        ? esUltima
          ? pendiente()
          : noAplica()
        : disponible(Math.sin((i + indiceSesion * 7) * 0.9) * 0.9)
      filas.push(aPrediccion(slot, semillas[(i + indiceSesion) % semillas.length], resultado))
    })
  })
  return filas
}

// -- piezas compartidas del payload ------------------------------------------

function resumen(vela: Vela, ahoraMs: number | null) {
  return {
    timestamp: vela.timestamp,
    fecha: vela.fecha,
    precio: vela.close,
    antiguedad_s: ahoraMs === null ? null : Math.max(0, Math.floor((ahoraMs - vela.timestamp) / 1000)),
  }
}

function calidadSimulada(ahora: number, cambios: Partial<CalidadModelo> = {}): CalidadModelo {
  return {
    disponible: true,
    motivo: null,
    model_type: 'mlp',
    feature_schema_version: '1',
    entrenado_en_ms: ahora - 2 * 60 * 60 * 1000,
    entrenado_en: formatearFecha(ahora - 2 * 60 * 60 * 1000),
    dispositivo_entrenamiento: 'cpu',
    macro_f1_test: 0.4313,
    banda_stable_pct: BANDA_PCT,
    acierto_direccional: {
      ratio: 0.4729,
      n: 1125,
      aciertos: 532,
      ic_95: [0.4387, 0.5078],
      fuente: 'informe_entrenamiento',
      ventaja_demostrada: false,
    },
    nota: NOTA_CALIDAD,
    ...cambios,
  }
}

function rendimientoSimulado(
  velaApertura: Vela,
  velaActual: Vela,
  ahora: number,
  cambios: Partial<Rendimiento> = {},
): Rendimiento {
  return {
    virtual: true,
    nota: NOTA_RENDIMIENTO,
    disponible: true,
    motivo: null,
    apertura_sesion: resumen(velaApertura, null),
    ultima_vela: resumen(velaActual, ahora),
    variacion_nasdaq_pct: 0.2824,
    rendimiento_ritech_pct: 0.5648,
    limite_perdida_pct: 1.25,
    umbral_perdida_alcanzado: false,
    ...cambios,
  }
}

const SALUD: Salud = { estado: 'ok', servicio: 'api', version: '0.1.0' }

interface DatosEscenario {
  status: EstadoSistema
  velas: Vela[]
  ultima: PrediccionUltima
  predicciones: Prediccion[]
  avisosHistorial: string[]
  rendimiento: Rendimiento
  calidad: CalidadModelo
}

// -- textos de razonamiento (misma estructura que el real de M1) -------------

const RAZONAMIENTO_REAL =
  'Atribución de la decisión (gradiente × entrada) sobre una ventana de 12 velas. ' +
  'Reparto del peso total por grupo: «volumen» 47,8 %, «rango y volatilidad» 28,3 %, ' +
  '«presión direccional» 20,8 %, «retorno» 3,1 %. El grupo de mayor peso fue «volumen» ' +
  '(47,8 %) y el menor «retorno» (3,1 %). Dentro de «volumen», lo que más pesó fue el ' +
  'volumen negociado (40,6 % del total). Las ventanas con más peso fueron la 1, 11, 12 ' +
  'de 12, es decir las más recientes de la ventana observada. Probabilidades de clase: ' +
  'DOWN 25,9 % / STABLE 46,2 % / UP 27,9 %. Decisión: UP con 27,9 % de confianza. ' +
  'Nota para auditoría (RF-303): este registro describe qué ponderó el modelo en su ' +
  'decisión; no es una causalidad de mercado ni una previsión justificada del ' +
  'comportamiento futuro del índice.'

function razonamientoDe(
  tendencia: string,
  confianza: string,
  reparto: string,
  probabilidad: string,
): string {
  return (
    'Atribución de la decisión (gradiente × entrada) sobre una ventana de 12 velas. ' +
    `Reparto del peso total por grupo: ${reparto} El grupo de mayor peso fue «volumen» y ` +
    'el menor «retorno». Las ventanas con más peso fueron las más recientes de la ' +
    `ventana observada. Probabilidades de clase: ${probabilidad} Decisión: ${tendencia} ` +
    `con ${confianza} de confianza. Nota para auditoría (RF-303): este registro describe ` +
    'qué ponderó el modelo en su decisión; no es una causalidad de mercado ni una ' +
    'previsión justificada del comportamiento futuro del índice.'
  )
}

const SEMILLA_ALTA: Semilla = {
  tendencia: 'UP',
  confianza: 0.86,
  razonamiento: razonamientoDe(
    'UP',
    '86,0 %',
    '«volumen» 52,1 %, «rango y volatilidad» 24,9 %, «presión direccional» 19,4 %, «retorno» 3,6 %. ',
    'DOWN 6,3 % / STABLE 7,7 % / UP 86,0 %.',
  ),
}

const SEMILLA_SETENTA: Semilla = {
  tendencia: 'UP',
  confianza: 0.71,
  razonamiento: razonamientoDe(
    'UP',
    '71,0 %',
    '«presión direccional» 38,2 %, «volumen» 34,7 %, «rango y volatilidad» 21,5 %, «retorno» 5,6 %. ',
    'STABLE 12,1 % / DOWN 16,8 % / UP 71,0 %.',
  ),
}

const SEMILLA_BAJA: Semilla = {
  tendencia: 'DOWN',
  confianza: 0.71,
  razonamiento: razonamientoDe(
    'DOWN',
    '71,0 %',
    '«presión direccional» 41,3 %, «volumen» 30,2 %, «rango y volatilidad» 23,4 %, «retorno» 5,1 %. ',
    'STABLE 12,1 % / DOWN 71,0 % / UP 16,9 %.',
  ),
}

const SEMILLA_ESTABLE: Semilla = {
  tendencia: 'STABLE',
  confianza: 0.62,
  razonamiento: razonamientoDe(
    'STABLE',
    '62,0 %',
    '«rango y volatilidad» 45,0 %, «volumen» 26,8 %, «presión direccional» 19,1 %, «retorno» 9,1 %. ',
    'UP 21,3 % / DOWN 16,7 % / STABLE 62,0 %.',
  ),
}

const SEMILLA_MEDIA: Semilla = {
  tendencia: 'UP',
  confianza: 0.52,
  razonamiento: RAZONAMIENTO_REAL,
}

const SEMILLAS_BASE: Semilla[] = [
  SEMILLA_SETENTA,
  SEMILLA_BAJA,
  SEMILLA_ESTABLE,
  SEMILLA_MEDIA,
]

// -- escenarios ---------------------------------------------------------------

function escenarioSenalOperable(ahora: number): DatosEscenario {
  const fin = Math.floor(ahora / VELA_MS) * VELA_MS
  const sesion = slotsDeSesion(fin, 78)
  const velas = construirVelas(sesion[sesion.length - 1], sesion.length, 715.3)
  const primera = velas[0]
  const ultimaVela = velas[velas.length - 1]
  const predicciones = construirHistorial([sesion], [SEMILLA_ALTA, ...SEMILLAS_BASE])
  const ultimaPrediccion = predicciones[0]
  const senal = senalDe(ultimaPrediccion.trend_prediction, ultimaPrediccion.confidence_score, PERFIL)

  return {
    status: {
      estado: 'AMARILLO',
      motivo: MOTIVO_ULTIMA_UP,
      mercado: { abierto: true, motivo: MOTIVO_ABIERTO, feriado: false, zona: ZONA_MERCADO, horario: HORARIO },
      ultima_vela: resumen(ultimaVela, ahora),
      retraso_max_s: 600,
      ia: { disponible: true, detalle: IA_OK },
      modo: { nombre: 'ASESOR', descripcion: DESCRIPCION_ASESOR, autonomo_disponible: false, motivo_bloqueo: MOTIVO_BLOQUEO },
      perfil: { nombre: PERFIL, umbral_confianza: UMBRAL, max_loss_day: 1.25, por_defecto: true },
      ultima_prediccion: ultimaPrediccion,
      senal,
      avisos: [],
    },
    velas,
    ultima: { encontrada: true, prediccion: ultimaPrediccion, senal, avisos: [] },
    predicciones,
    avisosHistorial: [],
    rendimiento: rendimientoSimulado(primera, ultimaVela, ahora, {
      variacion_nasdaq_pct: 0.87,
      rendimiento_ritech_pct: 1.74,
    }),
    calidad: calidadSimulada(ahora),
  }
}

function escenarioHistorialLargo(ahora: number): DatosEscenario {
  const fin = Math.floor(ahora / VELA_MS) * VELA_MS
  const todas = slotsDeSesion(fin, 180)
  // Agrupa por sesión (saltos > 1 vela = cambio de sesión) para aplicar la
  // regla de NO_APLICA de `resultados.py` en los cierres de cada día.
  const sesiones: number[][] = []
  for (const slot of todas) {
    const actual = sesiones[sesiones.length - 1]
    if (actual && actual[actual.length - 1] - slot === VELA_MS) {
      actual.push(slot)
    } else {
      sesiones.push([slot])
    }
  }
  const velasDeLaUltima = sesiones[0]
  const velas = construirVelas(velasDeLaUltima[velasDeLaUltima.length - 1], velasDeLaUltima.length, 708.9)
  const primera = velas[0]
  const ultimaVela = velas[velas.length - 1]
  const predicciones = construirHistorial(sesiones, SEMILLAS_BASE)
  const ultimaPrediccion = predicciones[0]
  const senal = senalDe(ultimaPrediccion.trend_prediction, ultimaPrediccion.confidence_score, PERFIL)

  return {
    status: {
      estado: 'CERRADO',
      motivo: MOTIVO_CIERRA,
      mercado: { abierto: false, motivo: MOTIVO_CIERRADO, feriado: false, zona: ZONA_MERCADO, horario: HORARIO },
      ultima_vela: resumen(ultimaVela, ahora),
      retraso_max_s: 600,
      ia: { disponible: true, detalle: IA_OK },
      modo: { nombre: 'ASESOR', descripcion: DESCRIPCION_ASESOR, autonomo_disponible: false, motivo_bloqueo: MOTIVO_BLOQUEO },
      perfil: { nombre: PERFIL, umbral_confianza: UMBRAL, max_loss_day: 1.25, por_defecto: false },
      ultima_prediccion: ultimaPrediccion,
      senal,
      avisos: [],
    },
    velas,
    ultima: { encontrada: true, prediccion: ultimaPrediccion, senal, avisos: [] },
    predicciones,
    avisosHistorial: [],
    rendimiento: rendimientoSimulado(primera, ultimaVela, ahora),
    calidad: calidadSimulada(ahora),
  }
}

function escenarioSinPredicciones(ahora: number): DatosEscenario {
  const fin = Math.floor(ahora / VELA_MS) * VELA_MS
  const sesion = slotsDeSesion(fin, 12)
  const velas = construirVelas(sesion[sesion.length - 1], sesion.length, 716.4)
  const primera = velas[0]
  const ultimaVela = velas[velas.length - 1]
  const senal = senalSinPrediccion(PERFIL)
  const aviso =
    'Sin predicción registrada todavía: la ventana de 12 velas de la sesión ' +
    'aún no está completa (no altera el estado).'

  return {
    status: {
      estado: 'VERDE',
      motivo: MOTIVO_VERDE,
      mercado: { abierto: true, motivo: MOTIVO_ABIERTO, feriado: false, zona: ZONA_MERCADO, horario: HORARIO },
      ultima_vela: resumen(ultimaVela, ahora),
      retraso_max_s: 600,
      ia: { disponible: true, detalle: IA_OK },
      modo: { nombre: 'ASESOR', descripcion: DESCRIPCION_ASESOR, autonomo_disponible: false, motivo_bloqueo: MOTIVO_BLOQUEO },
      perfil: { nombre: PERFIL, umbral_confianza: UMBRAL, max_loss_day: 1.25, por_defecto: true },
      ultima_prediccion: null,
      senal,
      avisos: [aviso],
    },
    velas,
    ultima: { encontrada: false, prediccion: null, senal, avisos: [] },
    predicciones: [],
    avisosHistorial: [],
    rendimiento: rendimientoSimulado(primera, ultimaVela, ahora, {
      variacion_nasdaq_pct: 0.05,
      rendimiento_ritech_pct: 0.1,
    }),
    calidad: calidadSimulada(ahora),
  }
}

const RAZONAMIENTO_EXTREMO =
  razonamientoDe(
    'DOWN',
    '99,99 %',
    '«presión direccional» 61,8 %, «volumen» 27,4 %, «rango y volatilidad» 9,7 %, «retorno» 1,1 %. ',
    'STABLE 0,001 % / DOWN 99,99 % / UP 0,009 %.',
  ) +
  ' Texto sin espacios para probar el ajuste de línea: ' +
  'QWERTYUIOPASDFGHJKLZXCVBNM0123456789'.repeat(4) +
  '. Referencia interna larga: ' +
  'https://intranet.ritech.local/modelos/mlp/v1/informes/2026-10-20/atribucion-detallada-por-grupo. ' +
  'Texto no latino (el razonamiento es campo libre de la base): ' +
  'モデルはボリュームと価格変動に最も重みを置いています؛ والقرار يعتمد على ضغط الشراء. ' +
  Array(6).fill('El volumen negociado domina la atribución cuando la ventana observada es reciente.').join(' ')

const AVISO_EXTREMO =
  'Series temporales no disponibles: el resultado real no se pudo calcular ' +
  "(HTTPConnectionPool(host='127.0.0.1', port=8086): Max retries exceeded with " +
  'url: /api/v2/write?org=ritech&bucket=nasdaq100&precision=ms (Caused by ' +
  "NewConnectionError('<urllib3.connection.HTTPConnection object at 0x7f3c9a2e1b40>: " +
  "Failed to establish a new connection: [Errno 111] Connection refused)'))."

function escenarioExtremos(ahora: number): DatosEscenario {
  const fin = Math.floor(ahora / VELA_MS) * VELA_MS
  const sesion = slotsDeSesion(fin, 78)
  const velas = construirVelas(sesion[sesion.length - 1], sesion.length, 1234.5, 1800)
  const primera = velas[0]
  const ultimaVela = velas[velas.length - 1]
  const extremo: Semilla = { tendencia: 'DOWN', confianza: 0.9999, razonamiento: RAZONAMIENTO_EXTREMO }
  const predicciones = construirHistorial([sesion], [extremo, ...SEMILLAS_BASE])
  const ultimaPrediccion = predicciones[0]
  const senal = senalDe(ultimaPrediccion.trend_prediction, ultimaPrediccion.confidence_score, 'AGGRESSIVE')

  return {
    status: {
      estado: 'AMARILLO',
      motivo: 'La última predicción es DOWN, no STABLE.',
      mercado: { abierto: true, motivo: MOTIVO_ABIERTO, feriado: false, zona: ZONA_MERCADO, horario: HORARIO },
      ultima_vela: resumen(ultimaVela, ahora),
      retraso_max_s: 600,
      ia: { disponible: true, detalle: IA_OK },
      modo: { nombre: 'ASESOR', descripcion: DESCRIPCION_ASESOR, autonomo_disponible: false, motivo_bloqueo: MOTIVO_BLOQUEO },
      perfil: { nombre: 'AGGRESSIVE', umbral_confianza: 0.65, max_loss_day: 1.25, por_defecto: false },
      ultima_prediccion: ultimaPrediccion,
      senal,
      avisos: [],
    },
    velas,
    ultima: { encontrada: true, prediccion: ultimaPrediccion, senal, avisos: [] },
    predicciones,
    avisosHistorial: [AVISO_EXTREMO],
    rendimiento: rendimientoSimulado(primera, ultimaVela, ahora, {
      variacion_nasdaq_pct: -2.14,
      rendimiento_ritech_pct: -2.14,
      umbral_perdida_alcanzado: true,
    }),
    calidad: calidadSimulada(ahora, {
      macro_f1_test: 0.9987,
      banda_stable_pct: 5,
      dispositivo_entrenamiento: 'cuda:0',
      entrenado_en_ms: ahora - 400 * 24 * 60 * 60 * 1000,
      entrenado_en: formatearFecha(ahora - 400 * 24 * 60 * 60 * 1000),
      acierto_direccional: {
        ratio: 1,
        n: 1,
        aciertos: 1,
        ic_95: [1, 1],
        fuente: 'informe_entrenamiento',
        ventaja_demostrada: true,
      },
      nota: `${NOTA_CALIDAD} ${NOTA_CALIDAD} ${NOTA_CALIDAD}`,
    }),
  }
}

const CONSTRUCTORES: Record<NombreEscenario, (ahora: number) => DatosEscenario> = {
  'senal-operable': escenarioSenalOperable,
  'historial-largo': escenarioHistorialLargo,
  'sin-predicciones': escenarioSinPredicciones,
  extremos: escenarioExtremos,
}

// -- servidor simulado --------------------------------------------------------

function entero(valor: string | null, defecto: number, min: number, max: number): number {
  const numero = Number(valor)
  if (!Number.isFinite(numero)) return defecto
  return Math.min(Math.max(Math.trunc(numero), min), max)
}

/** Responde cada ruta como lo haría la API de B1, con `ahora` de reloj. */
export function servirEscenario(
  escenario: NombreEscenario,
  ruta: string,
  ahora: number,
): unknown | undefined {
  const datos = CONSTRUCTORES[escenario](ahora)
  const url = new URL(ruta, 'http://pruebas.local')

  switch (url.pathname) {
    case '/api/status':
      return datos.status
    case '/api/candles': {
      const limite = entero(url.searchParams.get('limit'), 200, 1, 5000)
      const velas = datos.velas.slice(-limite)
      const respuesta: Velas = {
        velas,
        total: velas.length,
        aggregate: '5m',
        nota: NOTA_VELAS,
      }
      return respuesta
    }
    case '/api/predictions/latest':
      return datos.ultima
    case '/api/predictions': {
      const limite = entero(url.searchParams.get('limit'), 50, 1, 200)
      const offset = entero(url.searchParams.get('offset'), 0, 0, Number.MAX_SAFE_INTEGER)
      const respuesta: PrediccionPagina = {
        predicciones: datos.predicciones.slice(offset, offset + limite),
        total: datos.predicciones.length,
        limit: limite,
        offset,
        avisos: datos.avisosHistorial,
      }
      return respuesta
    }
    case '/api/yield':
      return datos.rendimiento
    case '/api/model-quality':
      return datos.calidad
    case '/api/health':
      return SALUD
    default:
      return undefined
  }
}

/** Resolutor para el registro de pruebas: se reconstruye en cada consulta. */
export function resolutorDe(escenario: NombreEscenario): ResolutorPruebas {
  return (ruta) => servirEscenario(escenario, ruta, Date.now())
}
