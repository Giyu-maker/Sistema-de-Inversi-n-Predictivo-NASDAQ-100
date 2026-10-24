#!/usr/bin/env node
/**
 * Mide RNF-004 («la interfaz web cargada a través de la intranet local debe
 * inicializarse completamente en el navegador en menos de 1.5 segundos»,
 * SRS §6.2) contra el build de producción.
 *
 * Cómo funciona:
 *
 * 1. Levanta Chromium headless con puerto de depuración remota y abre una
 *    pestaña con el protocolo de depuración de Chromium (CDP) sobre el
 *    WebSocket nativo de Node — sin dependencias externas.
 * 2. Navega N veces y lee de la línea de tiempo de rendimiento del navegador:
 *    `loadEventEnd` (inicialización completa), `first-contentful-paint`,
 *    cuándo llegó la primera respuesta real de `/api/status` (resource
 *    timing) y cuándo terminó de cargarse el chunk diferido del gráfico de
 *    velas (Recharts, cargado con `React.lazy`).
 * 3. Reporta cada corrida y la mediana. Sin vendor: la medición corre en la
 *    misma máquina que sirve, como en la intranet real de M2→M3.
 *
 * Uso (con `vite preview` — que hereda el proxy /api hacia 127.0.0.1:8010,
 * mismo origen que Nginx en M2 — ya levantado en 4173):
 *
 *   node scripts/bench_rnf004.mjs [url] [iteraciones]
 */
import { spawn } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const URL_DESTINO = process.argv[2] ?? 'http://127.0.0.1:4173/'
const ITERACIONES = Number(process.argv[3] ?? 5)
const PUERTO_CDP = 9333
const CHROMIUM = '/usr/bin/chromium'

const perfil = mkdtempSync(join(tmpdir(), 'bench-rnf004-'))

const chromium = spawn(
  CHROMIUM,
  [
    '--headless=new',
    `--remote-debugging-port=${PUERTO_CDP}`,
    `--user-data-dir=${perfil}`,
    '--no-sandbox',
    '--disable-gpu',
    '--no-first-run',
    '--hide-scrollbars',
    'about:blank',
  ],
  { stdio: ['ignore', 'ignore', 'pipe'] },
)

function esperar(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/** CDP mínimo: un socket, correlación por `id`, sin librerías. */
class ClienteCDP {
  constructor(url) {
    this.socket = new WebSocket(url)
    this.siguienteId = 0
    this.esperando = new Map()
  }

  async abrir() {
    await new Promise((resolve, reject) => {
      this.socket.addEventListener('open', resolve, { once: true })
      this.socket.addEventListener('error', reject, { once: true })
    })
    this.socket.addEventListener('message', (evento) => {
      const mensaje = JSON.parse(evento.data)
      const pendiente = this.esperando.get(mensaje.id)
      if (pendiente !== undefined) {
        this.esperando.delete(mensaje.id)
        pendiente(mensaje.result)
      }
    })
  }

  enviar(metodo, parametros = {}) {
    const id = ++this.siguienteId
    this.socket.send(JSON.stringify({ id, method: metodo, params: parametros }))
    return new Promise((resolve) => this.esperando.set(id, resolve))
  }

  cerrar() {
    this.socket.close()
  }
}

async function abrirPestana() {
  // Se crea en about:blank: si nace con la URL destino, la carga inicial puede
  // solaparse con la navegación posterior y su loadEventFired no llega.
  const respuesta = await fetch(
    `http://127.0.0.1:${PUERTO_CDP}/json/new?about:blank`,
    { method: 'PUT' },
  )
  const pestana = await respuesta.json()
  const cliente = new ClienteCDP(pestana.webSocketDebuggerUrl)
  await cliente.abrir()
  return { cliente, id: pestana.id }
}

async function cerrarPestana(id) {
  await fetch(`http://127.0.0.1:${PUERTO_CDP}/json/close/${id}`, { method: 'PUT' })
}

async function medirUna() {
  const { cliente, id } = await abrirPestana()
  await cliente.enviar('Page.enable')
  // Espera el load de esta navegación (con tope de 20 s por si el evento no llega).
  const carga = Promise.race([
    new Promise((resolve) => {
      const oyente = (evento) => {
        // El listener ve el MessageEvent crudo: el método CDP va dentro de `data`.
        if (JSON.parse(evento.data).method === 'Page.loadEventFired') {
          cliente.socket.removeEventListener('message', oyente)
          resolve()
        }
      }
      cliente.socket.addEventListener('message', oyente)
    }),
    esperar(20000),
  ])
  await cliente.enviar('Page.navigate', { url: URL_DESTINO })
  await carga
  await esperar(1200) // dejan llegar fetch y pintados posteriores al load

  const { result } = await cliente.enviar('Runtime.evaluate', {
    returnByValue: true,
    expression: `(() => {
      const nav = performance.getEntriesByType('navigation')[0]
      const pintado = performance.getEntriesByType('paint')
      const fcp = pintado.find((p) => p.name === 'first-contentful-paint')
      const api = performance.getEntriesByType('resource').find((r) => r.name.includes('/api/status'))
      const grafico = performance.getEntriesByType('resource').find((r) => r.name.includes('grafico-velas'))
      return {
        loadEventEnd: Math.round(nav.loadEventEnd),
        domContentLoaded: Math.round(nav.domContentLoadedEventEnd),
        fcp: fcp ? Math.round(fcp.startTime) : null,
        apiStatusMs: api ? Math.round(api.responseEnd) : null,
        chunkGraficoMs: grafico ? Math.round(grafico.responseEnd) : null,
        errores: performance.getEntriesByType('resource').filter((r) => r.name.includes('/api/') && r.responseStatus >= 400).length,
      }
    })()`,
  })
  await cerrarPestana(id)
  return result.value
}

const version = await (async () => {
  // Chromium tarda ~2-3 s en abrir el puerto de depuración: espera activa.
  for (let intento = 0; intento < 30; intento++) {
    try {
      const respuesta = await fetch(`http://127.0.0.1:${PUERTO_CDP}/json/version`)
      if (respuesta.ok) return await respuesta.json()
    } catch {
      // aún no escucha
    }
    await esperar(300)
  }
  throw new Error('Chromium no abrió el puerto de depuración en 9 s.')
})()
console.log(`Chromium: ${version.Browser}`)
console.log(`Destino:  ${URL_DESTINO}\n`)
const corridas = []
for (let i = 0; i < ITERACIONES; i++) {
  const m = await medirUna()
  corridas.push(m)
  console.log(
    `corrida ${i + 1}: carga completa ${m.loadEventEnd} ms · FCP ${m.fcp} ms · ` +
      `/api/status ${m.apiStatusMs} ms · chunk gráfico ${m.chunkGraficoMs} ms` +
      (m.errores > 0 ? ` · (${m.errores} respuestas /api con error)` : ''),
  )
}

const mediana = (campo) => {
  const valores = corridas.map((c) => c[campo] ?? Infinity).sort((a, b) => a - b)
  return valores[Math.floor(valores.length / 2)]
}
const carga = mediana('loadEventEnd')
console.log(
  `\nMediana: carga completa ${carga} ms · FCP ${mediana('fcp')} ms · ` +
    `chunk gráfico ${mediana('chunkGraficoMs')} ms`,
)
console.log(
  carga < 1500
    ? `RNF-004 CUMPLE: ${carga} ms < 1500 ms (mediana de ${ITERACIONES} corridas).`
    : `RNF-004 NO CUMPLE: ${carga} ms ≥ 1500 ms.`,
)

chromium.kill('SIGKILL')
// El perfil se libera con retardo tras matar al navegador: reintentos.
for (let intento = 0; intento < 5; intento++) {
  try {
    rmSync(perfil, { recursive: true, force: true })
    break
  } catch {
    await esperar(400)
  }
}
