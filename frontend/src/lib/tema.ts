/**
 * Tema claro/oscuro (SRS §5.1): la preferencia del sistema es el valor por
 * defecto; si la persona elige con el conmutador, su elección se guarda en
 * localStorage bajo la clave `tema` y manda en próximos arranques.
 *
 * La aplicación inicial (antes del primer pintado) vive en `public/tema.js`
 * para evitar el destello de tema equivocado; este módulo es el que usan los
 * componentes. Ambos comparten clave y criterio.
 */

export type Tema = 'claro' | 'oscuro'

const CLAVE = 'tema'

export function temaSistema(): Tema {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'oscuro' : 'claro'
}

export function temaGuardado(): Tema | null {
  const valor = localStorage.getItem(CLAVE)
  return valor === 'claro' || valor === 'oscuro' ? valor : null
}

export function temaActual(): Tema {
  return temaGuardado() ?? temaSistema()
}

export function aplicarTema(tema: Tema): void {
  document.documentElement.classList.toggle('dark', tema === 'oscuro')
}

/** Aplica el siguiente tema, lo persiste y devuelve cuál quedó activo. */
export function alternarTema(): Tema {
  const siguiente: Tema = temaActual() === 'oscuro' ? 'claro' : 'oscuro'
  localStorage.setItem(CLAVE, siguiente)
  aplicarTema(siguiente)
  return siguiente
}
