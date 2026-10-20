/* Tema antes del primer pintado (SRS §5.1): preferencia del sistema por
   defecto; localStorage('tema') manda si existe. Mismo criterio que
   src/lib/tema.ts — este archivo no pasa por el compilador, se carga tal cual
   desde index.html para no encuadrar el fondo con el tema equivocado. */
(function () {
  var tema = null
  try {
    tema = localStorage.getItem('tema')
  } catch {
    /* sin localStorage (modo privado): manda el sistema */
  }
  var oscuro =
    tema === 'oscuro' ||
    (tema !== 'claro' &&
      window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.classList.toggle('dark', oscuro)
})()
