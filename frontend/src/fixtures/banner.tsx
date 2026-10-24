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
      // En flujo normal, sin `fixed`: el banner se desplaza con la página y
      // no tapa la cabecera adherida (que se ancla al borde al pasar).
      className="border-b border-amber-300 bg-amber-100 px-4 py-1.5 text-center text-xs font-medium tracking-normal text-amber-950"
    >
      DATOS DE PRUEBA · escenario de prueba «{escenario}» — cifras simuladas,
      no son datos reales del mercado.
    </div>
  )
}
