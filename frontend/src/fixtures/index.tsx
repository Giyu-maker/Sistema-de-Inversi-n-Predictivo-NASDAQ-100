/**
 * Instalación de un escenario de prueba — solo llamado desde `main.tsx`,
 * en desarrollo y antes del primer render.
 */
import { instalarPruebas } from '../api/pruebas.ts'
import { BannerPruebas } from './banner.tsx'
import { esEscenario, resolutorDe } from './escenarios.ts'

/** Activa el escenario. Devuelve `false` si el nombre no existe. */
export function instalar(escenario: string): boolean {
  if (!esEscenario(escenario)) {
    return false
  }
  instalarPruebas(escenario, resolutorDe(escenario), () => <BannerPruebas />)
  return true
}
