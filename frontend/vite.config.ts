import path from 'node:path'
import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

const raiz = path.dirname(fileURLToPath(import.meta.url))

// En desarrollo Vite hace de proxy para que la UI hable con la API de M2 en el
// mismo origen (127.0.0.1:8010). En producción ese rol lo cumple Nginx desde
// M2 (decisión cerrada en el brief de la Fase B2): mismo origen en ambos casos.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { '@': path.resolve(raiz, 'src') } },
  server: {
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8010', changeOrigin: false },
    },
  },
})
