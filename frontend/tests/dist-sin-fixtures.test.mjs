/**
 * El bundle de producción no puede contener ni los datos de prueba ni el
 * banner: `main.tsx` importa `src/fixtures/` dinámicamente y solo detrás de
 * `import.meta.env.DEV`, de modo que `vite build` debe eliminar la rama.
 * Este test compila y hace grep de las marcas de desarrollo sobre `dist/`;
 * si alguna apareciera, el paquetito de fixtures se habría colado.
 */
import test from 'node:test'
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const frontend = dirname(dirname(fileURLToPath(import.meta.url)))
const dist = join(frontend, 'dist')

/** Marcas que solo existen en `src/fixtures/` (o en la rama DEV de main). */
const MARCAS = [
  'datos de prueba',
  'escenario de prueba',
  'historial-largo',
  'senal-operable',
  'sin-predicciones',
]

function archivosDe(directorio) {
  const salida = []
  for (const nombre of readdirSync(directorio)) {
    const ruta = join(directorio, nombre)
    if (statSync(ruta).isDirectory()) {
      salida.push(...archivosDe(ruta))
    } else {
      salida.push(ruta)
    }
  }
  return salida
}

test('el build de producción no arrastra datos de prueba ni el banner', () => {
  execFileSync('npm', ['run', 'build'], { cwd: frontend, stdio: 'pipe' })

  assert.ok(existsSync(join(dist, 'index.html')), 'dist/index.html debe existir')

  const encontradas = []
  for (const archivo of archivosDe(dist)) {
    const contenido = readFileSync(archivo, 'utf8').toLowerCase()
    for (const marca of MARCAS) {
      if (contenido.includes(marca)) {
        encontradas.push(`${marca} → ${archivo}`)
      }
    }
  }

  assert.deepEqual(
    encontradas,
    [],
    `marcas de desarrollo presentes en dist/:\n${encontradas.join('\n')}`,
  )
})
