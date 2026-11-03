/**
 * Esquemas Zod de las 7 rutas de la API de M2 — espejo de
 * `src/endpoints/esquemas.py` (contrato RF-304, informe B1).
 *
 * Dos reglas, tomadas del brief de la Fase B2:
 *
 * 1. **Los nombres del contrato se mantienen literales** (`trend_prediction`,
 *    `confidence_score`, `technical_rationale`): es la frontera que ya consume
 *    `src/ai/serve.py`; renombrarla aquí sería romper el contrato.
 * 2. **Los campos desconocidos se toleran**: `z.object` por defecto elimina
 *    las claves que no conoce sin fallar. La API usa `extra="forbid"` hacia
 *    dentro (un campo nuevo debe romper el test del backend), pero la interfaz
 *    no debe romperse si el contrato crece — solo valida que todo lo que lee
 *    esté presente y con el tipo correcto.
 *
 * Si una respuesta no cumple el contrato, el cliente no devuelve «medios
 * datos»: devuelve el estado `error` con «Respuesta inesperada del servidor»
 * (ver `cliente.ts`). Nada de esto se ejecuta en el navegador de producción
 * sin pasar por `fetch`: los esquemas viajan en el bundle (peso ~13 kB gzip),
 * que es el costo de no mostrar cifras inventadas si el backend cambia.
 */
import { z } from 'zod'

// -- piezas comunes ---------------------------------------------------------

export const esquemaVela = z.object({
  timestamp: z.number().int(), // epoch ms
  fecha: z.string(), // DD/MM/YYYY HH:mm:ss (hora de Colombia)
  open: z.number().nonnegative(),
  high: z.number().nonnegative(),
  low: z.number().nonnegative(),
  close: z.number().nonnegative(),
  volume: z.number().nonnegative(),
})

/** Vela sin OHLC: para el estado y el rendimiento bastan cierre y hora. */
export const esquemaVelaResumen = z.object({
  timestamp: z.number().int(),
  fecha: z.string(),
  precio: z.number(),
  antiguedad_s: z.number().int().nullable(),
})

/** Veredicto calculado en el backend (`src/security/perfiles.py`, D7). */
export const esquemaSenal = z.object({
  operable: z.boolean(),
  motivo: z.string(),
  umbral: z.number(),
  perfil: z.string(),
  tendencia: z.string().nullable(),
  confianza: z.number().nullable(),
})

export const esquemaResultadoReal = z.object({
  estado: z.string(), // DISPONIBLE | PENDIENTE | SIN_DATOS | NO_APLICA (texto mostrable)
  motivo: z.string(),
  retorno_pct: z.number().nullable(),
  etiqueta: z.string().nullable(),
  banda_pct: z.number().nullable(),
})

export const esquemaPrediccion = z.object({
  id: z.string(),
  prediction_time: z.number().int(), // epoch ms del cierre de la última vela (D2)
  fecha: z.string(),
  trend_prediction: z.string(), // UP | DOWN | STABLE
  confidence_score: z.number(),
  technical_rationale: z.string(),
  resultado_real: esquemaResultadoReal.nullable(),
})

// -- /api/health ------------------------------------------------------------

export const esquemaSalud = z.object({
  estado: z.string(),
  servicio: z.string(),
  version: z.string(),
})

// -- /api/status ------------------------------------------------------------

export const esquemaMercado = z.object({
  abierto: z.boolean(),
  motivo: z.string(),
  feriado: z.boolean(),
  zona: z.string(),
  horario: z.string(),
})

export const esquemaIa = z.object({
  disponible: z.boolean(),
  detalle: z.string(),
})

export const esquemaModo = z.object({
  nombre: z.string(),
  descripcion: z.string(),
  autonomo_disponible: z.boolean(),
  motivo_bloqueo: z.string(),
})

export const esquemaPerfil = z.object({
  nombre: z.string(),
  umbral_confianza: z.number(),
  max_loss_day: z.number(),
  por_defecto: z.boolean(),
})

// -- /api/kill-switch ---------------------------------------------------------

/**
 * Estado del bloqueo. `activo` puede ser `null`: **no verificado** no es lo
 * mismo que **desactivado**, y la interfaz no debe pintarlos igual.
 *
 * `detalle` lo redacta el backend (`KillSwitchEngine.resumen`): la UI solo
 * lo pinta, nunca compone texto de emergencia.
 */
export const esquemaKillSwitch = z.object({
  activo: z.boolean().nullable(),
  detalle: z.string(),
  motivo: z.string().nullable(),
  origen: z.string().nullable(),
  liquidacion_estado: z.string(),
  liquidaciones: z.number().int(),
  disparado_en: z.number().int().nullable(),
  reinicio_en: z.number().int().nullable(),
})

/** Respuesta de `POST /api/kill-switch`: estado + métricas del disparo. */
export const esquemaDisparo = esquemaKillSwitch.extend({
  primera_vez: z.boolean(),
  ms_bloqueo: z.number(),
  ms_liquidacion: z.number(),
  ordenes_simuladas: z.number().int(),
  persistencia_ok: z.boolean(),
  auditoria_ok: z.boolean(),
})

/** Respuesta de `POST /api/kill-switch/reset`. */
export const esquemaReinicio = z.object({
  reiniciado: z.boolean(),
  detalle: z.string(),
  intentos_reinicio: z.number().int(),
})

export type KillSwitch = z.infer<typeof esquemaKillSwitch>
export type Disparo = z.infer<typeof esquemaDisparo>
export type Reinicio = z.infer<typeof esquemaReinicio>

export const esquemaEstadoSistema = z.object({
  estado: z.string(), // VERDE | AMARILLO | CERRADO | ROJO (Kill-Switch, RF-501)
  motivo: z.string(),
  mercado: esquemaMercado,
  ultima_vela: esquemaVelaResumen.nullable(),
  retraso_max_s: z.number().int(),
  ia: esquemaIa,
  modo: esquemaModo,
  perfil: esquemaPerfil,
  ultima_prediccion: esquemaPrediccion.nullable(),
  senal: esquemaSenal.nullable(),
  kill_switch: esquemaKillSwitch,
  avisos: z.array(z.string()),
})

// -- /api/candles ------------------------------------------------------------

export const esquemaVelas = z.object({
  velas: z.array(esquemaVela),
  total: z.number().int(),
  aggregate: z.string(),
  nota: z.string(),
})

// -- /api/predictions --------------------------------------------------------

export const esquemaPrediccionUltima = z.object({
  encontrada: z.boolean(),
  prediccion: esquemaPrediccion.nullable(),
  senal: esquemaSenal,
  avisos: z.array(z.string()),
})

export const esquemaPrediccionPagina = z.object({
  predicciones: z.array(esquemaPrediccion),
  total: z.number().int(),
  limit: z.number().int(),
  offset: z.number().int(),
  avisos: z.array(z.string()),
})

// -- /api/yield --------------------------------------------------------------

export const esquemaRendimiento = z.object({
  virtual: z.boolean(),
  nota: z.string(),
  disponible: z.boolean(),
  motivo: z.string().nullable(),
  apertura_sesion: esquemaVelaResumen.nullable(),
  ultima_vela: esquemaVelaResumen.nullable(),
  variacion_nasdaq_pct: z.number().nullable(),
  rendimiento_ritech_pct: z.number().nullable(),
  limite_perdida_pct: z.number(),
  umbral_perdida_alcanzado: z.boolean(),
})

// -- /api/model-quality -------------------------------------------------------

export const esquemaAciertoDireccional = z.object({
  ratio: z.number().nullable(),
  n: z.number().int().nullable(),
  aciertos: z.number().int().nullable(),
  ic_95: z.array(z.number()).nullable(),
  fuente: z.string(),
  ventaja_demostrada: z.boolean().nullable(),
})

export const esquemaCalidadModelo = z.object({
  disponible: z.boolean(),
  motivo: z.string().nullable(),
  model_type: z.string().nullable(),
  feature_schema_version: z.string().nullable(),
  entrenado_en_ms: z.number().int().nullable(),
  entrenado_en: z.string().nullable(),
  dispositivo_entrenamiento: z.string().nullable(),
  macro_f1_test: z.number().nullable(),
  banda_stable_pct: z.number().nullable(),
  acierto_direccional: esquemaAciertoDireccional.nullable(),
  nota: z.string(),
})

// -- tipos inferidos ----------------------------------------------------------

export type Vela = z.infer<typeof esquemaVela>
export type VelaResumen = z.infer<typeof esquemaVelaResumen>
export type Senal = z.infer<typeof esquemaSenal>
export type ResultadoReal = z.infer<typeof esquemaResultadoReal>
export type Prediccion = z.infer<typeof esquemaPrediccion>
export type Salud = z.infer<typeof esquemaSalud>
export type EstadoSistema = z.infer<typeof esquemaEstadoSistema>
export type Velas = z.infer<typeof esquemaVelas>
export type PrediccionUltima = z.infer<typeof esquemaPrediccionUltima>
export type PrediccionPagina = z.infer<typeof esquemaPrediccionPagina>
export type Rendimiento = z.infer<typeof esquemaRendimiento>
export type CalidadModelo = z.infer<typeof esquemaCalidadModelo>
