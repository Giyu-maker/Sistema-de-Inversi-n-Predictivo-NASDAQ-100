/**
 * Registro de datos de prueba — la pieza mínima que viaja en el bundle.
 *
 * Este archivo se importa desde `cliente.ts`, así que `vite build` lo incluye
 * siempre. Es deliberadamente vacío: no contiene escenarios, ni datos, ni
 * textos de prueba — solo el enganche. Los escenarios y el banner viven en
 * `src/fixtures/`, que `main.tsx` importa dinámicamente detrás de
 * `import.meta.env.DEV`; la rama queda eliminada del bundle de producción y
 * `dist/` queda sin rastros (lo verifica `tests/dist-sin-fixtures.test.mjs`).
 */
import type { ReactNode } from 'react'

/** Devuelve el cuerpo simulado de una ruta (`/api/status?x=1`), o `undefined`. */
export type ResolutorPruebas = (ruta: string) => unknown | undefined

/** Pinta el banner del escenario: se ejecuta como función, no como componente. */
export type PintarBanner = () => ReactNode

let resolutor: ResolutorPruebas | null = null
let banner: PintarBanner | null = null
let nombre: string | null = null

/** Lo llama `main.tsx` en desarrollo, antes del primer render. */
export function instalarPruebas(
  escenario: string,
  resolver: ResolutorPruebas,
  pintarBanner: PintarBanner,
): void {
  resolutor = resolver
  banner = pintarBanner
  nombre = escenario
}

/** `true` cuando un escenario de desarrollo está activo. */
export function hayPruebas(): boolean {
  return resolutor !== null
}

/** Nombre del escenario activo (para el banner), o `null`. */
export function escenarioActivo(): string | null {
  return nombre
}

/** Función que pinta el banner registrado, o `null` fuera de desarrollo. */
export function bannerPruebas(): PintarBanner | null {
  return banner
}

/** Cuerpo simulado para `ruta`, o `undefined` si el escenario no la cubre. */
export function consultarPrueba(ruta: string): unknown | undefined {
  return resolutor === null ? undefined : resolutor(ruta)
}
