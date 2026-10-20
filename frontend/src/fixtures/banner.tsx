import type { ReactElement } from 'react'

import { escenarioActivo } from '../api/pruebas.ts'

/**
 * Banner permanente mientras un escenario de prueba está activo.
 *
 * «DATOS DE PRUEBA» y «escenario de prueba» son también las marcas que
 * `tests/dist-sin-fixtures.test.mjs` busca en `dist/`: si alguna vez este
 * módulo llegara al bundle de producción, la verificación falla.
 */
export function BannerPruebas(): ReactElement {
  const escenario = escenarioActivo() ?? 'desconocido'
  return (
    <div
      role="status"
      className="fixed inset-x-0 top-0 z-50 border-b border-amber-300 bg-amber-100 px-4 py-1.5 text-center text-xs font-medium tracking-normal text-amber-950"
    >
      DATOS DE PRUEBA · escenario de prueba «{escenario}» — cifras simuladas,
      no son datos reales del mercado.
    </div>
  )
}
