/**
 * Lógica pura del gráfico de velas 5M (B2.4) — fuera del componente para que
 * sea testeable sin DOM ni Recharts.
 *
 * Las fechas vienen de la API ya formateadas en hora de Colombia
 * («DD/MM/YYYY HH:mm:ss», SRS §5.6), así que el día se extrae por posición
 * de cadena: cero cálculo de zona horaria en el navegador. Las velas de la
 * API llegan en orden cronológico ascendente.
 */
import type { Vela } from '../api/esquemas.ts'

/** «23/10/2026 14:55:00» → «23/10/2026» (la fecha es la parte fija inicial). */
export function diaDe(fecha: string): string {
  return fecha.slice(0, 10)
}

/** «23/10/2026 14:55:00» → «14:55» (hora de la vela, para el eje X). */
export function horaDe(fecha: string): string {
  return fecha.slice(11, 16)
}

/**
 * Solo las velas del último día presente en la serie — «última sesión» es la
 * vista por defecto del contrato de dirección. Si todo el rango es un solo
 * día, devuelve las mismas velas.
 */
export function ultimaSesion(velas: Vela[]): Vela[] {
  const ultima = velas.at(-1)
  if (ultima === undefined) {
    return []
  }
  const dia = diaDe(ultima.fecha)
  return velas.filter((vela) => diaDe(vela.fecha) === dia)
}

export interface ResumenVelas {
  /** Apertura de la primera vela mostrada. */
  apertura: number
  /** Cierre de la última vela mostrada. */
  cierre: number
  minimo: number
  maximo: number
  /** Variación apertura→cierre en puntos porcentuales; `null` si no hay base. */
  variacionPct: number | null
}

/**
 * Resumen textual accesible del conjunto mostrado (último cierre, variación
 * del periodo y rango): el operador lo lee sin mirar la forma del gráfico y
 * un lector de pantalla lo anuncia sin depender del SVG.
 */
export function resumirVelas(velas: Vela[]): ResumenVelas | null {
  const primera = velas[0]
  const ultima = velas.at(-1)
  if (primera === undefined || ultima === undefined) {
    return null
  }
  const apertura = primera.open
  const cierre = ultima.close
  return {
    apertura,
    cierre,
    minimo: Math.min(...velas.map((vela) => vela.low)),
    maximo: Math.max(...velas.map((vela) => vela.high)),
    variacionPct: apertura === 0 ? null : ((cierre - apertura) / apertura) * 100,
  }
}
