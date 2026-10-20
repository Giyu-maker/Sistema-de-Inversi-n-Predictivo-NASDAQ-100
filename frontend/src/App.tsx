/**
 * Scaffold de la Fase B2: la pantalla de operación se construye por
 * sub-bloques sobre este shell (contrato de dirección en
 * `.impeccable/surfaces/frontend-src-app-tsx.md`). El único enganche de
 * B2.2 es el banner de datos de prueba: se registra en `main.tsx` antes del
 * primer render y solo existe en desarrollo (`src/fixtures/`).
 */
import { bannerPruebas } from './api/pruebas.ts'

export default function App() {
  const banner = bannerPruebas()
  return (
    <main className="min-h-dvh bg-background text-foreground">
      {banner ? banner() : null}
      <div className="mx-auto max-w-5xl px-6 py-16">
        <h1 className="text-2xl font-semibold">RiTech NASDAQ-100</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          Panel de operación — en construcción (Fase B2).
        </p>
      </div>
    </main>
  )
}
