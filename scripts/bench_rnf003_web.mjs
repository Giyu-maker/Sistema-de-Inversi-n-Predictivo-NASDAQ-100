#!/usr/bin/env node
/**
 * RNF-003 componente (b): latencia p50/p95 de las rutas que consume la UI,
 * contra NGINX en el mismo origen (127.0.0.1:8080), como en la intranet.
 *
 * Secuencial a propósito (sin concurrencia): mide la latencia que verá el
 * navegador en cada petición del ciclo. Incluye el proxy /api → api:8010.
 *
 * Uso: node scripts/bench_rnf003_web.mjs [base] [por_ruta]
 */
const BASE = process.argv[2] ?? 'http://127.0.0.1:8080'
const POR_RUTA = Number(process.argv[3] ?? 50)

const RUTAS = [
  '/api/status',
  '/api/candles?limit=200',
  '/api/predictions/latest',
  '/api/predictions?limit=50&offset=0',
  '/api/yield',
  '/api/model-quality',
  '/api/health',
]

function percentil(muestras, p) {
  const orden = [...muestras].sort((a, b) => a - b)
  const i = Math.min(orden.length - 1, Math.ceil((p / 100) * orden.length) - 1)
  return orden[Math.max(0, i)]
}

async function main() {
  const porRuta = new Map()
  const global = []
  for (const ruta of RUTAS) {
    const ms = []
    for (let i = 0; i < POR_RUTA; i += 1) {
      const t0 = performance.now()
      const r = await fetch(BASE + ruta, { cache: 'no-store' })
      await r.arrayBuffer()
      const t = performance.now() - t0
      if (!r.ok) throw new Error(`${ruta} → HTTP ${r.status}`)
      ms.push(t)
      global.push(t)
    }
    porRuta.set(ruta, ms)
  }
  const fmt = (n) => n.toFixed(2).replace('.', ',')
  console.log(`RNF-003 (b) · rutas de la UI contra Nginx (${BASE}) · n=${POR_RUTA} por ruta, secuencial`)
  console.log(`${'RUTA'.padEnd(36)}${'p50 (ms)'.padStart(10)}${'p95 (ms)'.padStart(10)}${'máx (ms)'.padStart(10)}`)
  for (const [ruta, ms] of porRuta) {
    console.log(
      `${ruta.padEnd(36)}${fmt(percentil(ms, 50)).padStart(10)}${fmt(percentil(ms, 95)).padStart(10)}${fmt(Math.max(...ms)).padStart(10)}`,
    )
  }
  console.log(
    `${'TOTAL (todas las peticiones)'.padEnd(36)}${fmt(percentil(global, 50)).padStart(10)}${fmt(percentil(global, 95)).padStart(10)}${fmt(Math.max(...global)).padStart(10)}`,
  )
}

main().catch((e) => {
  console.error('FALLO:', e.message)
  process.exit(1)
})
