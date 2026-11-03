/**
 * Lógica pura del control del Kill-Switch — RF-501 y RNF-001.
 *
 * Vive en `lib/` y no en el componente a propósito: es la única parte que se
 * puede probar sin React ni red, y es donde se codifica la regla que el SRS
 * exige — **máximo 2 toques** para ejecutar el Kill-Switch desde el panel.
 *
 * El texto lo pinta el backend (`KillSwitchEngine.resumen`); aquí solo se
 * deciden estados y etiquetas cortas de UI. Nunca se redacta una frase de
 * emergencia en la interfaz.
 */

/** Estado del botón de disparo. */
export type FaseDisparo = 'reposo' | 'confirmar' | 'enviando'

/** Lo que le puede pasar al botón. */
export type EventoDisparo =
  | 'pulsar' // 1.er toque: preguntar
  | 'confirmar' // 2.º toque: ejecutar
  | 'cancelar'
  | 'exito'
  | 'fallo'

/**
 * Máquina de estados del disparo, en dos toques (RNF-001).
 *
 * - `reposo` + `pulsar` → `confirmar`: **nunca** se dispara de un solo
 *   toque; el botón de emergencia también puede pulsarse por error.
 * - `confirmar` + `confirmar` → `enviando`: el segundo toque ejecuta.
 * - `enviando` + `fallo` → `confirmar`: queda el error a la vista y el
 *   reintento es un toque; volver a `reposo` escondería lo que falló.
 * - Cualquier evento fuera de lugar se ignora (idempotente).
 */
export function siguienteFase(fase: FaseDisparo, evento: EventoDisparo): FaseDisparo {
  if (fase === 'reposo') return evento === 'pulsar' ? 'confirmar' : 'reposo'
  if (fase === 'confirmar') {
    if (evento === 'confirmar') return 'enviando'
    if (evento === 'cancelar') return 'reposo'
    if (evento === 'fallo') return 'confirmar'
    return 'confirmar'
  }
  // 'enviando': solo el resultado mueve el estado.
  if (evento === 'exito') return 'reposo'
  if (evento === 'fallo') return 'confirmar'
  return 'enviando'
}

/** Etiqueta corta del control según el estado del bloqueo. */
export interface EtiquetaKillSwitch {
  texto: string
  clase: string
}

/**
 * Texto y clase del control. Los tres casos se distinguen a propósito:
 * activo (rojo), sin verificar (ámbar) y desactivado (neutro) no pueden
 * verse igual — «no lo sé» no es «todo bien».
 */
export function etiquetaDe(killSwitch: { activo: boolean | null } | null): EtiquetaKillSwitch {
  if (killSwitch === null || killSwitch.activo === null) {
    return { texto: 'Estado sin verificar', clase: 'bg-ambar-fondo text-ambar-texto' }
  }
  if (killSwitch.activo) {
    return { texto: 'Kill-Switch activo', clase: 'bg-rojo-fondo text-rojo-texto' }
  }
  return { texto: 'Kill-Switch', clase: 'bg-muted text-muted-foreground' }
}

/** ¿Se está esperando una respuesta? (mientras tanto, nada se puede pulsar). */
export function ocupado(fase: FaseDisparo): boolean {
  return fase === 'enviando'
}
