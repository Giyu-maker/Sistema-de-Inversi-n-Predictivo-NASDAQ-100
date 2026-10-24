/**
 * Cabecera fija del panel (B2.3): estado en lenguaje llano + icono, motivo,
 * antigüedad del dato, franja de sesión y conmutador de tema.
 *
 * Reglas del brief de la Fase B2, todas acá:
 *
 * - **Región de anuncio**: `role="status"` + `aria-live="polite"` contiene
 *   SOLO el estado y su motivo. El latido repinta cada 10 s pero el texto no
 *   cambia, y un lector de pantalla anuncia cambios, no repeticiones: el
 *   sondeo no se cuela en el canal de anuncios.
 * - **Verde/ámbar son los únicos colores semánticos** (SRS §10.3). «Mercado
 *   cerrado» es la rutina de la noche y la mañana y pinta neutro: lo normal
 *   nunca se lee como error.
 * - **Franja de sesión SOLO horario regular** 09:30–16:00 ET (CR-002): sin
 *   ventanas de exclusión porque RF-204 no existe en el SRS. La posición
 *   actual es el marcador; el motivo del mercado ya viene del backend.
 * - **Antigüedad del dato fuera del canal de anuncio**: cambia cada 10 s y
 *   no debe repetirse en voz alta.
 */
import { useEffect, useState } from 'react'
import {
  CheckCircle,
  Clock,
  Info,
  Moon,
  Sun,
  Warning,
} from '@phosphor-icons/react'

import type { Fase } from '../api/cliente.ts'
import type { EstadoSistema } from '../api/esquemas.ts'
import { posicionSesion } from '../lib/formato.ts'
import { alternarTema, temaActual } from '../lib/tema.ts'
import type { Tema } from '../lib/tema.ts'

/** Duración de la sesión regular en minutos (09:30 → 16:00 ET). */
const DURACION_SESION_MIN = 390

interface EtiquetaEstado {
  texto: string
  clase: string
}

/**
 * Nombre en lenguaje llano de cada estado del contrato. El backend manda los
 * enums (`VERDE`/`AMARILLO`/`CERRADO`) en el campo `estado` y su frase en
 * `motivo`; esto solo pinta la etiqueta del chip, sin tocar los textos.
 */
const ETIQUETAS: Record<string, EtiquetaEstado> = {
  VERDE: { texto: 'Sistema normal', clase: 'bg-verde-fondo text-verde-texto' },
  AMARILLO: { texto: 'Atención', clase: 'bg-ambar-fondo text-ambar-texto' },
  CERRADO: { texto: 'Mercado cerrado', clase: 'bg-muted text-muted-foreground' },
}

const ETIQUETA_DESCONOCIDA: EtiquetaEstado = {
  texto: 'Estado desconocido',
  clase: 'bg-muted text-muted-foreground',
}

/** «hace 42 s» / «hace 7 min» / «hace 3 h» — el dato crudo viene en segundos. */
function antiguedadEnPalabras(segundos: number): string {
  if (segundos < 60) return `hace ${segundos} s`
  if (segundos < 3600) return `hace ${Math.floor(segundos / 60)} min`
  return `hace ${Math.floor(segundos / 3600)} h`
}

function iconoDeEstado(codigo: string, clase: string): React.ReactElement {
  if (codigo === 'VERDE') return <CheckCircle weight="bold" className={clase} />
  if (codigo === 'AMARILLO') return <Warning weight="bold" className={clase} />
  return <Clock weight="bold" className={clase} />
}

export interface PropsCabecera {
  estado: EstadoSistema | null
  fase: Fase
  mensaje: string | null
}

export function Cabecera({ estado, fase, mensaje }: PropsCabecera) {
  const [tema, setTema] = useState<Tema>(temaActual)
  // El reloj vive en estado (no en el render) y late cada minuto: la
  // posición en la franja avanza sin repintar por cada fotograma.
  const [ahora, setAhora] = useState(() => Date.now())

  useEffect(() => {
    const periodo = 60_000
    const latido = window.setInterval(() => setAhora(Date.now()), periodo)
    return () => window.clearInterval(latido)
  }, [])

  const etiqueta = estado === null ? ETIQUETA_DESCONOCIDA : (ETIQUETAS[estado.estado] ?? ETIQUETA_DESCONOCIDA)
  const avisoDeRed =
    estado !== null && fase === 'error'
      ? (mensaje ?? 'No se pudo contactar con el servidor.')
      : null

  // La franja representa la sesión regular de HOY: sin día hábil o en feriado
  // (lo decide el backend) no hay sesión que representar.
  const posicion = posicionSesion(ahora)
  const haySesionHoy = posicion.diaUtil && !estado?.mercado.feriado
  const restantes = DURACION_SESION_MIN - posicion.minutos

  return (
    <header className="sticky top-0 z-40 border-b border-border bg-background">
      <div className="mx-auto flex max-w-5xl flex-col gap-2 px-6 py-3">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          {/* Única región viva: anuncia SOLO cambios de estado y motivo. */}
          <div role="status" aria-live="polite" className="flex min-w-0 items-center gap-2">
            {estado === null ? (
              <span className="text-sm text-muted-foreground">
                {fase === 'error'
                  ? (mensaje ?? 'No se pudo contactar con el servidor.')
                  : 'Esperando la primera respuesta del sistema…'}
              </span>
            ) : (
              <>
                <span
                  className={`inline-flex shrink-0 items-center gap-1.5 rounded-md px-2 py-0.5 text-sm font-medium ${etiqueta.clase}`}
                >
                  {iconoDeEstado(estado.estado, 'h-4 w-4')}
                  {etiqueta.texto}
                </span>
                <p className="min-w-0 text-sm text-foreground">{estado.motivo}</p>
              </>
            )}
          </div>

          <div className="ml-auto flex shrink-0 items-center gap-3">
            <p className="text-xs text-muted-foreground">
              {estado?.ultima_vela === null || estado?.ultima_vela === undefined ? (
                'Sin datos de precios'
              ) : (
                <>
                  Última vela:{' '}
                  <span className="dato text-foreground">{estado.ultima_vela.fecha}</span>
                  {estado.ultima_vela.antiguedad_s === null
                    ? null
                    : ` · ${antiguedadEnPalabras(estado.ultima_vela.antiguedad_s)}`}
                </>
              )}
            </p>
            <button
              type="button"
              onClick={() => setTema(alternarTema())}
              aria-label={`Cambiar a tema ${tema === 'claro' ? 'oscuro' : 'claro'}`}
              className="rounded-md border border-border p-1.5 text-muted-foreground hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring"
            >
              {tema === 'claro' ? (
                <Moon weight="bold" className="h-4 w-4" />
              ) : (
                <Sun weight="bold" className="h-4 w-4" />
              )}
            </button>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          {/* Franja de sesión: SOLO horario regular (CR-002). */}
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className="dato">09:30</span>
            <div
              className="relative h-1 w-32 rounded-full bg-muted"
              role="img"
              aria-label={
                haySesionHoy
                  ? estado?.mercado.abierto === true
                    ? `Sesión en curso: faltan ${Math.floor(restantes / 60)} horas y ${restantes % 60} minutos para el cierre`
                    : 'La sesión regular de hoy aún no comienza'
                  : !posicion.diaUtil
                    ? 'Hoy no hay sesión regular del mercado: fin de semana'
                    : estado?.mercado.feriado === true
                      ? 'Hoy no hay sesión regular del mercado: feriado'
                      : 'La sesión regular de hoy ya terminó'
              }
            >
              <div
                className="absolute inset-y-0 left-0 rounded-full bg-foreground"
                style={{ width: `${(haySesionHoy ? posicion.avance : 0) * 100}%` }}
              />
              {haySesionHoy ? (
                <span
                  className="absolute top-1/2 h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full border border-background bg-foreground"
                  style={{ left: `${posicion.avance * 100}%` }}
                />
              ) : null}
            </div>
            <span className="dato">16:00</span>
            <span>ET</span>
            {estado?.mercado.abierto === true ? (
              <span>
                · faltan{' '}
                {restantes >= 60
                  ? `${Math.floor(restantes / 60)} h ${restantes % 60} min`
                  : `${restantes} min`}
              </span>
            ) : null}
          </div>

          {/* Avisos: conviene saberlos, no deciden el estado (fuera del vivo). */}
          {estado?.avisos.map((aviso) => (
            <p key={aviso} className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Info weight="bold" className="h-3.5 w-3.5 shrink-0" />
              {aviso}
            </p>
          ))}
          {avisoDeRed !== null ? (
            <p className="flex items-center gap-1.5 text-xs text-ambar-texto">
              <Warning weight="bold" className="h-3.5 w-3.5 shrink-0" />
              {avisoDeRed} Mostrando el último dato recibido.
            </p>
          ) : null}
        </div>
      </div>
    </header>
  )
}
