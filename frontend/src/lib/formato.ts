/**
 * Formato de fechas, números y porcentajes — es-CO con zonas IANA reales.
 *
 * Reglas del brief de la Fase B2:
 *
 * - **Fechas `DD/MM/YYYY HH:mm:ss`** (SRS §5.6) armadas desde `formatToParts`,
 *   no desde el orden del locale. Lo que llega de la API ya viene en hora de
 *   Colombia (`America/Bogota`); la sesión del mercado se piensa en
 *   `America/New_York`. Nada de offsets fijos: EE. UU. cambia de horario dos
 *   veces al año, y el cierre de las 16:00 ET no siempre es la misma hora en
 *   Bogotá (hasta el 01/11/2026, 15:00; después, 16:00).
 * - **Porcentaje con espacio fino (U+202F)** antes del símbolo: «27,9 %».
 * - La **confianza** es un ratio [0..1] que aquí se formatea como porcentaje;
 *   cómo decirlo en texto llano lo decide la UI (nunca «probabilidad de
 *   subida»: el modelo no expone probabilidades por clase como campos).
 */

export const ZONA_COLOMBIA = 'America/Bogota'
export const ZONA_MERCADO = 'America/New_York'
/** U+202F: espacio fino sin ancho, el que va antes del signo de porcentaje. */
export const ESPACIO_FINO = '\u202F'

/** Sesión regular NYSE (09:30–16:00 ET), en minutos del día de mercado. */
export const APERTURA_MINUTOS = 9 * 60 + 30
export const CIERRE_MINUTOS = 16 * 60

const creadoresDeFecha = new Map<string, Intl.DateTimeFormat>()

function creadorDeFecha(zona: string): Intl.DateTimeFormat {
  let creador = creadoresDeFecha.get(zona)
  if (!creador) {
    creador = new Intl.DateTimeFormat('es-CO', {
      timeZone: zona,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hourCycle: 'h23',
    })
    creadoresDeFecha.set(zona, creador)
  }
  return creador
}

function partesDeFecha(epochMs: number, zona: string): Record<string, string> {
  const acumulado: Record<string, string> = {}
  for (const parte of creadorDeFecha(zona).formatToParts(epochMs)) {
    if (parte.type !== 'literal') {
      acumulado[parte.type] = parte.value
    }
  }
  return acumulado
}

const creadoresDeDia = new Map<string, Intl.DateTimeFormat>()

function creadorDeDia(zona: string): Intl.DateTimeFormat {
  let creador = creadoresDeDia.get(zona)
  if (!creador) {
    // `en-US` para recibir el nombre corto del día en ASCII ('Sat', 'Sun'):
    // el día de la semana es una decisión de calendario, no un texto de UI.
    creador = new Intl.DateTimeFormat('en-US', { timeZone: zona, weekday: 'short' })
    creadoresDeDia.set(zona, creador)
  }
  return creador
}

/** `epochMs` → «DD/MM/YYYY HH:mm:ss» en la zona pedida (por defecto Colombia). */
export function formatearFecha(epochMs: number, zona: string = ZONA_COLOMBIA): string {
  const p = partesDeFecha(epochMs, zona)
  return `${p.day}/${p.month}/${p.year} ${p.hour}:${p.minute}:${p.second}`
}

/** Minutos transcurridos del día en `zona` (0–1439). */
export function minutosDelDia(epochMs: number, zona: string = ZONA_MERCADO): number {
  const p = partesDeFecha(epochMs, zona)
  return Number(p.hour) * 60 + Number(p.minute)
}

/** `false` si cae en sábado o domingo en `zona`. */
export function diaUtil(epochMs: number, zona: string = ZONA_MERCADO): boolean {
  const dia = creadorDeDia(zona).format(epochMs)
  return dia !== 'Sat' && dia !== 'Sun'
}

export interface PosicionSesion {
  /** Día hábil en Nueva York (los feriados los decide el backend). */
  diaUtil: boolean
  /** Entre 09:30 y 16:00 ET de un día hábil. */
  dentro: boolean
  /** Minutos transcurridos de la sesión, recortados a 0–390. */
  minutos: number
  /** Avance 0–1 de la sesión (0 antes de abrir, 1 después de cerrar). */
  avance: number
}

const DURACION_SESION = CIERRE_MINUTOS - APERTURA_MINUTOS

/** Posición de un instante dentro de la sesión regular del mercado. */
export function posicionSesion(epochMs: number): PosicionSesion {
  const delDia = minutosDelDia(epochMs)
  const transcurridos = Math.min(
    Math.max(delDia - APERTURA_MINUTOS, 0),
    DURACION_SESION,
  )
  return {
    diaUtil: diaUtil(epochMs),
    dentro: diaUtil(epochMs) && delDia >= APERTURA_MINUTOS && delDia < CIERRE_MINUTOS,
    minutos: transcurridos,
    avance: transcurridos / DURACION_SESION,
  }
}

const creadoresDeNumero = new Map<string, Intl.NumberFormat>()

function creadorDeNumero(decimales: number): Intl.NumberFormat {
  let creador = creadoresDeNumero.get(String(decimales))
  if (!creador) {
    creador = new Intl.NumberFormat('es-CO', {
      minimumFractionDigits: decimales,
      maximumFractionDigits: decimales,
    })
    creadoresDeNumero.set(String(decimales), creador)
  }
  return creador
}

/** `1234567.891` → «1.234.567,89» (coma decimal, punto de millares, es-CO). */
export function formatearNumero(valor: number, decimales = 2): string {
  return creadorDeNumero(decimales).format(valor)
}

/** `27.9` → «27,9 %» (con espacio fino). `valorPct` ya está en puntos %). */
export function formatearPorcentaje(valorPct: number, decimales = 1): string {
  return `${formatearNumero(valorPct, decimales)}${ESPACIO_FINO}%`
}

/** Ratio [0..1] → «75 %». Para confianza y umbrales del perfil. */
export function formatearRatio(ratio: number, decimales = 1): string {
  return formatearPorcentaje(ratio * 100, decimales)
}

/**
 * Confianza [0..1] sin fingir certeza (revisión de capturas P1.10): si el
 * redondeo a un decimal llegaría a 100, se escribe «más de 99,9 %» — un
 * modelo probabilístico nunca está al 100 %.
 */
export function formatearConfianza(ratio: number): string {
  if (Math.round(ratio * 1000) / 10 >= 100) return `más de 99,9${ESPACIO_FINO}%`
  return formatearRatio(ratio)
}

/**
 * «15:55 ET» — hora de Nueva York para el rótulo doble de la última vela
 * (P1.4): la interfaz muestra hora de Colombia y la sesión se vive en ET.
 */
export function formatearHoraET(epochMs: number): string {
  const p = partesDeFecha(epochMs, ZONA_MERCADO)
  return `${p.hour}:${p.minute} ET`
}

/**
 * ¿La predicción pertenece a un día de sesión distinto al de la última vela?
 * (P1.5). La comparación es por FECHA en America/New_York de cada instante —
 * sin reloj de por medio, así el render es puro—; como las predicciones se
 * derivan de las velas, «distinto día» siempre es «anterior». Referencia
 * `null` (aún no hay velas): no hay nada que calificar.
 */
export function sesionPrevia(epochPrediccion: number, epochReferencia: number | null): boolean {
  if (epochReferencia === null) return false
  const diaPrediccion = formatearFecha(epochPrediccion, ZONA_MERCADO).slice(0, 10)
  const diaReferencia = formatearFecha(epochReferencia, ZONA_MERCADO).slice(0, 10)
  return diaPrediccion !== diaReferencia
}

/**
 * Porcentaje SIEMPRE con signo explícito, para rendimientos y variaciones:
 * «+0,03 %» / «−1,25 %». El signo es texto (neutro: ni verde ni rojo, el rojo
 * lo reserva el Kill-Switch); el cero se firma solo como «0,00 %». El signo
 * menos es U+2212 (−), no el guion del teclado: comparte ancho con el más en
 * las cifras tabulares y no lee como raya de tabla.
 */
export function formatearPorcentajeFirmado(valorPct: number, decimales = 2): string {
  const absoluto = formatearPorcentaje(Math.abs(valorPct), decimales)
  if (valorPct > 0) return `+${absoluto}`
  if (valorPct < 0) return `−${absoluto}`
  return absoluto
}
