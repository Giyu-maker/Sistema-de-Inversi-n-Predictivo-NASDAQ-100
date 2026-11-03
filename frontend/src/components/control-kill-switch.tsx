/**
 * Control del Kill-Switch en la cabecera — RF-501 y RNF-001 (≤ 2 toques).
 *
 * Reglas del brief y del SRS, todas acá:
 *
 * - **Dos toques, nunca uno**: pulsar y confirmar. La máquina de estados
 *   está en `src/lib/killSwitch.ts` (y probada en `tests/killSwitch.test.mjs`).
 * - **Los textos vienen del backend**: el `detalle` del bloqueo lo redacta
 *   `KillSwitchEngine.resumen`. Aquí no se compone ninguna frase de
 *   emergencia, y el botón solo traduce la respuesta a «lo que pasó».
 * - **Rojo con texto + icono** (SRS §10.3) y parpadeo lento desactivado por
 *   `prefers-reduced-motion` (clase `.parpadeo-lento` de `index.css`).
 * - **Sin roles de usuario** (CR-006): el reinicio lo ve cualquiera que
 *   pueda ver el panel, pero exige la clave de reinicio. Esa es la brecha
 *   documentada, no un olvido.
 * - **Máximo privilegio invertido**: disparar no pide nada (RF-501: en una
 *   emergencia no se pide credencial); reiniciar sí (RF-503).
 */
import { useEffect, useRef, useState } from 'react'
import { Prohibit } from '@phosphor-icons/react'

import type { KillSwitch } from '../api/esquemas.ts'
import { disparar, reiniciar } from '../api/killSwitch.ts'
import { etiquetaDe, siguienteFase } from '../lib/killSwitch.ts'
import type { FaseDisparo } from '../lib/killSwitch.ts'

interface Props {
  /** Estado del bloqueo según `/api/status`; `null` mientras no llega. */
  killSwitch: KillSwitch | null
  /** Pide un nuevo estado tras una escritura (el latido tarda 10 s). */
  refrescar: () => void
}

const CLASE_BOTON =
  'rounded-md border border-border px-2.5 py-1.5 text-xs font-medium ' +
  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ' +
  'disabled:cursor-not-allowed disabled:opacity-60'

export function ControlKillSwitch({ killSwitch, refrescar }: Props) {
  const [fase, setFase] = useState<FaseDisparo>('reposo')
  const [mensaje, setMensaje] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [panelReinicio, setPanelReinicio] = useState(false)
  const [clave, setClave] = useState('')
  const [reiniciando, setReiniciando] = useState(false)
  const peticion = useRef<AbortController | null>(null)

  // Cancelar en vuelo al desmontar: la respuesta de un botón de emergencia
  // no debe llegar a un componente que ya no existe.
  useEffect(() => () => peticion.current?.abort(), [])

  const activo = killSwitch?.activo === true
  const etiqueta = etiquetaDe(killSwitch)

  async function alPulsar(): Promise<void> {
    if (fase === 'reposo') {
      setMensaje(null)
      setError(null)
      setFase(siguienteFase(fase, 'pulsar'))
      return
    }
    if (fase !== 'confirmar') {
      return
    }
    setFase(siguienteFase(fase, 'confirmar'))
    peticion.current?.abort()
    peticion.current = new AbortController()
    const respuesta = await disparar(peticion.current.signal)
    if (respuesta.estado === 'cancelado') {
      return
    }
    if (respuesta.estado === 'ok') {
      setFase(siguienteFase('enviando', 'exito'))
      setMensaje(respuesta.datos.detalle)
      setError(null)
      refrescar()
      return
    }
    setFase(siguienteFase('enviando', 'fallo'))
    setError(respuesta.mensaje)
  }

  async function alReiniciar(): Promise<void> {
    if (clave.trim().length === 0 || reiniciando) {
      return
    }
    setReiniciando(true)
    peticion.current?.abort()
    peticion.current = new AbortController()
    const respuesta = await reiniciar(clave, peticion.current.signal)
    setReiniciando(false)
    if (respuesta.estado === 'cancelado') {
      return
    }
    if (respuesta.estado === 'ok') {
      setMensaje(respuesta.datos.detalle)
      setError(null)
      setClave('')
      setPanelReinicio(false)
      refrescar()
      return
    }
    // 401 (clave incorrecta), 429 (demasiados intentos) o 503: el backend
    // ya manda la frase en español.
    setError(respuesta.mensaje)
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex items-center gap-2">
        {activo ? (
          <span
            className={`inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium ${etiqueta.clase} parpadeo-lento motion-reduce:animate-none`}
          >
            <Prohibit weight="bold" className="h-3.5 w-3.5" aria-hidden="true" />
            {etiqueta.texto}
          </span>
        ) : null}

        {activo ? (
          <button
            type="button"
            onClick={() => {
              setPanelReinicio((abierto) => !abierto)
              setError(null)
            }}
            className={`${CLASE_BOTON} border-rojo-texto text-rojo-texto hover:bg-rojo-fondo`}
            aria-expanded={panelReinicio}
          >
            Reiniciar
          </button>
        ) : (
          <button
            type="button"
            onClick={() => void alPulsar()}
            disabled={fase === 'enviando'}
            aria-label={
              fase === 'confirmar'
                ? 'Confirmar: detener el sistema ahora'
                : 'Detener el sistema (Kill-Switch)'
            }
            className={`${CLASE_BOTON} ${
              fase === 'confirmar'
                ? 'border-rojo-texto bg-rojo-fondo text-rojo-texto'
                : 'text-muted-foreground hover:bg-rojo-fondo hover:text-rojo-texto'
            }`}
          >
            {fase === 'enviando'
              ? 'Deteniendo…'
              : fase === 'confirmar'
                ? '¿Sí, detener?'
                : 'Kill-Switch'}
          </button>
        )}
      </div>

      {panelReinicio && activo ? (
        <form
          className="flex items-center gap-1.5"
          onSubmit={(evento) => {
            evento.preventDefault()
            void alReiniciar()
          }}
        >
          <label className="sr-only" htmlFor="clave-reinicio">
            Clave de reinicio
          </label>
          <input
            id="clave-reinicio"
            type="password"
            autoComplete="current-password"
            value={clave}
            onChange={(evento) => setClave(evento.target.value)}
            placeholder="Clave de reinicio"
            className="dato w-40 rounded-md border border-border bg-background px-2 py-1.5 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          <button
            type="submit"
            disabled={reiniciando || clave.trim().length === 0}
            className={`${CLASE_BOTON} text-foreground`}
          >
            {reiniciando ? 'Reiniciando…' : 'Confirmar'}
          </button>
          <button
            type="button"
            onClick={() => {
              setPanelReinicio(false)
              setClave('')
              setError(null)
            }}
            className={`${CLASE_BOTON} text-muted-foreground`}
          >
            Cancelar
          </button>
        </form>
      ) : null}

      {/* Anuncio único: estado del intento, en lenguaje llano del backend. */}
      <p
        role="status"
        aria-live="polite"
        className="max-w-[22rem] text-right text-xs text-muted-foreground"
      >
        {error ?? mensaje ?? ''}
      </p>
    </div>
  )
}
