#!/usr/bin/env node
/**
 * RNF-003 componente (c): render del cliente tras recibir datos, medido con
 * `performance.measure` en la propia app (marcas en useEstado/useCiclo):
 *
 *   render-estado-tras-datos  → latido de /api/status (cada 10 s)
 *   render-ciclo-tras-datos   → velas + predicción + rendimiento + calidad
 *
 * Abre el build de producción servido por NGINX por CDP (WebSocket nativo de
 * Node, sin dependencias) y espera `segundos` para acumular muestras.
 *
 * Uso: node scripts/bench_rnf003_ui.mjs [url] [segundos]
 */
import { spawn } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const URL_DESTINO = process.argv[2] ?? 'http://127.0.0.1:8080/'
const SEGUNDOS = Number(process.argv[3] ?? 70)
const PUERTO_CDP = 9355
const CHROMIUM = '/usr/bin/chromium'

const perfil = mkdtempSync(join(tmpdir(), 'bench-rnf003-ui-'))
const dormir = (ms) => new Promise((r) => setTimeout(r, ms))

function percentil(muestras, p) {
  const orden = [...muestras].sort((a, b) => a - b)
  const i = Math.min(orden.length - 1, Math.ceil((p / 100) * orden.length) - 1)
  return orden[Math.max(0, i)]
}

let id = 0
const pendientes = new Map()
let ws

function enviar(method, params = {}) {
  return new Promise((resolver, rechazar) => {
    id += 1
    pendientes.set(id, { resolver, rechazar })
    ws.send(JSON.stringify({ id, method, params }))
  })
}

async function evaluar(expresion) {
  const r = await enviar('Runtime.evaluate', { expression: expresion, returnByValue: true })
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.text)
  return r.result.value
}

async function main() {
  const chromium = spawn(
    CHROMIUM,
    [
      '--headless=new',
      `--remote-debugging-port=${PUERTO_CDP}`,
      `--user-data-dir=${perfil}`,
      '--no-sandbox',
      '--disable-gpu',
      '--window-size=1440,900',
      'about:blank',
    ],
    { stdio: 'ignore' },
  )
  let listo = false
  for (let i = 0; i < 60 && !listo; i += 1) {
    try {
      listo = (await fetch(`http://127.0.0.1:${PUERTO_CDP}/json/version`)).ok
    } catch {
      /* subiendo */
    }
    if (!listo) await dormir(500)
  }
  if (!listo) throw new Error('Chromium no abrió el CDP')

  const destino = await fetch(`http://127.0.0.1:${PUERTO_CDP}/json/new?about:blank`, {
    method: 'PUT',
  }).then((r) => r.json())
  ws = new WebSocket(destino.webSocketDebuggerUrl)
  await new Promise((r) => ws.addEventListener('open', r))
  ws.addEventListener('message', (ev) => {
    const m = JSON.parse(ev.data)
    if (m.id !== undefined && pendientes.has(m.id)) {
      const { resolver, rechazar } = pendientes.get(m.id)
      pendientes.delete(m.id)
      m.error ? rechazar(new Error(JSON.stringify(m.error))) : resolver(m.result)
    }
  })
  await enviar('Page.enable')
  await enviar('Runtime.enable')

  const carga = new Promise((r) => {
    ws.addEventListener('message', (ev) => {
      const m = JSON.parse(ev.data)
      if (m.method === 'Page.loadEventFired') r()
    })
  })
  console.log(`Navegando a ${URL_DESTINO} y acumulando ${SEGUNDOS} s de muestras…`)
  await enviar('Page.navigate', { url: URL_DESTINO })
  await carga
  await dormir(SEGUNDOS * 1000)

  const medidas = await evaluar(
    `performance.getEntriesByType('measure')
      .filter((m) => m.name.endsWith('tras-datos'))
      .map((m) => ({ nombre: m.name, ms: m.duration }))`,
  )

  const grupos = new Map()
  for (const { nombre, ms } of medidas) {
    if (!grupos.has(nombre)) grupos.set(nombre, [])
    grupos.get(nombre).push(ms)
  }
  const fmt = (n) => n.toFixed(2).replace('.', ',')
  console.log(`RNF-003 (c) · render del cliente tras datos (${URL_DESTINO}) · ventana ${SEGUNDOS} s`)
  console.log(`${'MEDIDA'.padEnd(30)}${'n'.padStart(4)}${'p50 (ms)'.padStart(10)}${'p95 (ms)'.padStart(10)}${'máx (ms)'.padStart(10)}`)
  if (grupos.size === 0) console.log('(sin muestras: ¿la página no recibió datos?)')
  for (const [nombre, ms] of grupos) {
    console.log(
      `${nombre.padEnd(30)}${String(ms.length).padStart(4)}${fmt(percentil(ms, 50)).padStart(10)}${fmt(percentil(ms, 95)).padStart(10)}${fmt(Math.max(...ms)).padStart(10)}`,
    )
  }

  ws.close()
  chromium.kill()
  try {
    await dormir(500)
    rmSync(perfil, { recursive: true, force: true })
  } catch {
    /* el perfil temporal lo recoge el SO: no es motivo para fallar */
  }
}

main().catch((e) => {
  console.error('FALLO:', e)
  process.exit(1)
})
