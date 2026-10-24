/**
 * Gráfico de velas de 5 minutos de QQQ (B2.4).
 *
 * Reglas del brief de la Fase B2, todas acá:
 *
 * - **Sin colores semánticos**: el verde/ámbar pintan estados del sistema y
 *   el rojo lo reserva el Kill-Switch (SRS §10.3). Aquí solo tinta: alza =
 *   cuerpo vacío con filete, baja = cuerpo relleno. La leyenda lo explica en
 *   texto, así que no hace falta color para leerlo.
 * - **Forma personalizada en Recharts** (única dependencia nueva de B2.4,
 *   fijada en 3.10.1 y cargada con `React.lazy`: la cabecera y la tarjeta de
 *   señal no esperan a este chunk).
 * - **Navegación por teclado** con `accessibilityLayer` (flechas para recorrer
 *   las velas), más **resumen textual accesible** (último cierre, variación y
 *   rango) escrito antes del SVG en el orden del DOM.
 * - **Por defecto, la última sesión**: las ~200 velas del ciclo cubren varios
 *   días; el operador decide si quiere ver todo el rango cargado.
 *
 * Las fechas vienen de la API ya formateadas en hora de Colombia, así que el
 * eje X usa la hora de la vela sin conversiones de zona horaria.
 */
import { useMemo, useState } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import type { Velas } from '../api/esquemas.ts'
import { formatearNumero, formatearPorcentajeFirmado } from '../lib/formato.ts'
import { diaDe, horaDe, resumirVelas, ultimaSesion } from '../lib/velas.ts'

/** Datum del gráfico: el rango [mínimo, máximo] dibuja la mecha completa. */
interface DatoVela {
  hora: string
  fecha: string
  rango: [number, number]
  apertura: number
  cierre: number
  maximo: number
  minimo: number
  volumen: number
  sube: boolean
}

interface PropsFormaVela {
  x?: number
  y?: number
  width?: number
  height?: number
  payload?: Partial<DatoVela>
  // Recharts esparce el datum sobre las props del shape (además de payload).
  apertura?: number
  cierre?: number
  maximo?: number
  minimo?: number
  sube?: boolean
}

/**
 * Vela personalizada: mecha de mínimo a máximo y cuerpo de apertura a
 * cierre. Alza = cuerpo vacío (fill de la tarjeta) con filete de 1px; baja =
 * cuerpo relleno de tinta. Un doji (apertura = cierre) queda como filete de
 * 1px, que es como se dibuja en papel.
 */
function FormaVela(props: PropsFormaVela) {
  const x = props.x ?? 0
  const y = props.y ?? 0
  const width = props.width ?? 0
  const height = props.height ?? 0
  const dato = props.payload
  const apertura = dato?.apertura ?? props.apertura
  const cierre = dato?.cierre ?? props.cierre
  const maximo = dato?.maximo ?? props.maximo
  const minimo = dato?.minimo ?? props.minimo
  const sube = dato?.sube ?? props.sube

  if (
    apertura === undefined ||
    cierre === undefined ||
    maximo === undefined ||
    minimo === undefined ||
    sube === undefined
  ) {
    return null
  }

  const centro = x + width / 2
  const amplitud = maximo - minimo
  // El bar [mínimo, máximo] escala linealmente: y = máximo, y + height = mínimo.
  const yDe = (precio: number): number =>
    y + ((maximo - precio) / (amplitud || 1)) * height
  const yApertura = yDe(apertura)
  const yCierre = yDe(cierre)
  const arriba = Math.min(yApertura, yCierre)
  const altoCuerpo = Math.max(Math.abs(yCierre - yApertura), 1)

  return (
    <g>
      <line
        x1={centro}
        x2={centro}
        y1={y}
        y2={y + height}
        className="stroke-foreground"
        strokeWidth={1}
      />
      <rect
        x={x}
        y={arriba}
        width={width}
        height={altoCuerpo}
        strokeWidth={sube ? 1 : 0}
        className={sube ? 'fill-card stroke-foreground' : 'fill-foreground'}
      />
    </g>
  )
}

interface PropsTooltip {
  active?: boolean
  payload?: Array<{ payload?: DatoVela }>
}

function ContenidoTooltip({ active, payload }: PropsTooltip) {
  const dato = payload?.[0]?.payload
  if (active !== true || dato === undefined) {
    return null
  }
  return (
    <div className="rounded-lg border border-border bg-card px-3 py-2 text-xs shadow-none">
      <p className="dato text-muted-foreground">{dato.fecha}</p>
      <dl className="mt-1 space-y-0.5">
        <div className="flex justify-between gap-4">
          <dt className="text-muted-foreground">Apertura</dt>
          <dd className="dato">{formatearNumero(dato.apertura)}</dd>
        </div>
        <div className="flex justify-between gap-4">
          <dt className="text-muted-foreground">Máximo</dt>
          <dd className="dato">{formatearNumero(dato.maximo)}</dd>
        </div>
        <div className="flex justify-between gap-4">
          <dt className="text-muted-foreground">Mínimo</dt>
          <dd className="dato">{formatearNumero(dato.minimo)}</dd>
        </div>
        <div className="flex justify-between gap-4">
          <dt className="text-muted-foreground">Cierre</dt>
          <dd className="dato">{formatearNumero(dato.cierre)}</dd>
        </div>
        <div className="flex justify-between gap-4">
          <dt className="text-muted-foreground">Volumen</dt>
          <dd className="dato">{formatearNumero(dato.volumen, 0)}</dd>
        </div>
      </dl>
    </div>
  )
}

interface PropsGraficoVelas {
  velas: Velas | null
  cargando: boolean
  mensaje: string | null
}

type Vista = 'sesion' | 'rango'

export default function GraficoVelas({ velas, cargando, mensaje }: PropsGraficoVelas) {
  const [vista, setVista] = useState<Vista>('sesion')

  const mostradas = useMemo(() => {
    if (velas === null) {
      return []
    }
    return vista === 'sesion' ? ultimaSesion(velas.velas) : velas.velas
  }, [velas, vista])

  const datos = useMemo<DatoVela[]>(() => {
    const etiquetaHora = vista === 'sesion'
    return mostradas.map((vela) => ({
      hora: etiquetaHora ? horaDe(vela.fecha) : `${diaDe(vela.fecha).slice(0, 5)} ${horaDe(vela.fecha)}`,
      fecha: vela.fecha,
      rango: [vela.low, vela.high],
      apertura: vela.open,
      cierre: vela.close,
      maximo: vela.high,
      minimo: vela.low,
      volumen: vela.volume,
      sube: vela.close >= vela.open,
    }))
  }, [mostradas, vista])

  const resumen = useMemo(() => resumirVelas(mostradas), [mostradas])
  const hayMasDeUnDia = velas !== null && ultimaSesion(velas.velas).length < velas.velas.length

  // Margen vertical: las mechas pegadas al borde son ilegibles. Con una sola
  // vela el rango es casi cero, así que el margen tiene suelo proporcional al
  // precio para que el eje no se contraiga sobre la vela.
  const minimos = datos.map((d) => d.minimo)
  const maximos = datos.map((d) => d.maximo)
  const piso = minimos.length > 0 ? Math.min(...minimos) : 0
  const techo = maximos.length > 0 ? Math.max(...maximos) : 1
  const margen = Math.max((techo - piso) * 0.08, Math.abs(techo) * 0.002) || 1

  const etiquetaPeriodo = vista === 'sesion' ? 'de la sesión' : 'del rango cargado'

  return (
    <section aria-labelledby="titulo-velas" className="rounded-2xl border border-border bg-card p-6">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2">
        <h2 id="titulo-velas" className="text-sm font-medium text-muted-foreground">
          Velas de 5 minutos · QQQ
        </h2>
        {hayMasDeUnDia ? (
          <div role="group" aria-label="Rango del gráfico" className="flex gap-1 text-xs">
            <button
              type="button"
              onClick={() => setVista('sesion')}
              aria-pressed={vista === 'sesion'}
              className="rounded-md border border-border px-2 py-1 text-muted-foreground hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring aria-pressed:bg-secondary aria-pressed:text-foreground"
            >
              Última sesión
            </button>
            <button
              type="button"
              onClick={() => setVista('rango')}
              aria-pressed={vista === 'rango'}
              className="rounded-md border border-border px-2 py-1 text-muted-foreground hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring aria-pressed:bg-secondary aria-pressed:text-foreground"
            >
              Todo el rango
            </button>
          </div>
        ) : null}
      </div>

      {velas === null ? (
        <p className="mt-4 text-sm text-muted-foreground">
          {cargando ? 'Cargando velas…' : (mensaje ?? 'No se pudo cargar el gráfico de velas.')}
        </p>
      ) : datos.length === 0 ? (
        <p className="mt-4 text-sm text-muted-foreground">Sin velas registradas todavía.</p>
      ) : (
        <>
          {/* Resumen textual: se lee antes del SVG en el orden del DOM y
              también sirve de leyenda accesible del conjunto mostrado. */}
          <p className="mt-3 text-sm text-foreground">
            Último cierre <span className="dato">{formatearNumero(resumen?.cierre ?? 0)}</span> ·
            variación {etiquetaPeriodo}{' '}
            <span className="dato">
              {resumen?.variacionPct === null || resumen?.variacionPct === undefined
                ? '—'
                : formatearPorcentajeFirmado(resumen.variacionPct)}
            </span>{' '}
            · rango <span className="dato">{formatearNumero(resumen?.minimo ?? 0)}</span>–
            <span className="dato">{formatearNumero(resumen?.maximo ?? 0)}</span>
          </p>

          <div className="mt-2 h-[280px] text-border" aria-hidden="true">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={datos}
                margin={{ top: 8, right: 8, bottom: 0, left: 0 }}
                accessibilityLayer
              >
                <CartesianGrid stroke="currentColor" vertical={false} />
                <XAxis
                  dataKey="hora"
                  minTickGap={28}
                  tickLine={false}
                  axisLine={{ className: 'stroke-border' }}
                  tick={{ className: 'fill-muted-foreground' }}
                  tickMargin={6}
                />
                <YAxis
                  width={70}
                  domain={[piso - margen, techo + margen]}
                  tickLine={false}
                  axisLine={{ className: 'stroke-border' }}
                  tick={{ className: 'fill-muted-foreground' }}
                  tickFormatter={(valor: number) => formatearNumero(valor, 2)}
                />
                <Tooltip
                  cursor={{ stroke: 'currentColor', strokeDasharray: '3 3' }}
                  content={<ContenidoTooltip />}
                />
                <Bar
                  dataKey="rango"
                  shape={<FormaVela />}
                  isAnimationActive={false}
                  // Con pocas velas en pantalla Recharts ensancha el slot hasta
                  // el ancho del eje: el cuerpo se acota para que una vela
                  // aislada no se dibuje como un rectángulo gigante.
                  maxBarSize={16}
                />
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* Leyenda en texto + icono (nunca color solo). */}
          <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-muted-foreground">
            <span className="flex items-center gap-1.5">
              <svg width="10" height="14" viewBox="0 0 10 14" aria-hidden="true">
                <rect x="0.5" y="0.5" width="9" height="13" className="fill-card stroke-foreground" strokeWidth="1" />
              </svg>
              Alza: cuerpo vacío con filete
            </span>
            <span className="flex items-center gap-1.5">
              <svg width="10" height="14" viewBox="0 0 10 14" aria-hidden="true">
                <rect x="0" y="0" width="10" height="14" className="fill-foreground" />
              </svg>
              Baja: cuerpo relleno
            </span>
            <span>
              {mostradas.length} {mostradas.length === 1 ? 'vela' : 'velas'} de 5 minutos
            </span>
          </div>

          <p className="mt-2 text-xs text-muted-foreground">{velas.nota}</p>
        </>
      )}
    </section>
  )
}
