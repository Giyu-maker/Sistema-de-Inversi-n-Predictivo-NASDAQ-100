import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

const raiz = document.getElementById('root')!

function arrancar(): void {
  createRoot(raiz).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
}

// Datos de prueba: solo en desarrollo y solo si la URL lo pide (`?pruebas=…`).
// La importación dinámica queda detrás de `import.meta.env.DEV`, de modo que
// `vite build` elimina la rama entera y `dist/` queda sin los datos ni el
// banner (lo verifica `tests/dist-sin-fixtures.test.mjs`). El arranque espera
// a la importación para que el primer render ya tenga el banner registrado.
if (import.meta.env.DEV) {
  const escenario = new URLSearchParams(window.location.search).get('pruebas')
  if (escenario !== null) {
    void import('./fixtures/index.tsx')
      .then(({ instalar }) => {
        if (!instalar(escenario)) {
          console.warn(`Escenario de prueba desconocido: «${escenario}».`)
        }
      })
      .catch(() => {
        console.warn(`No se pudieron cargar los datos de prueba («${escenario}»).`)
      })
      .finally(arrancar)
  } else {
    arrancar()
  }
} else {
  arrancar()
}
