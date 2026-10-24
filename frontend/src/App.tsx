/**
 * Pantalla de operación de la Fase B2: cabecera fija (estado, motivo,
 * antigüedad, franja de sesión y tema) más la tarjeta de señal, enganchadas
 * a los hooks de datos de B2.2. Los bloques siguientes (velas, rendimiento e
 * historial) entran debajo en B2.4. Contrato de dirección en
 * `.impeccable/surfaces/frontend-src-app-tsx.md`.
 */
import { Cabecera } from './components/cabecera.tsx'
import { TarjetaSenal } from './components/tarjeta-senal.tsx'
import { bannerPruebas } from './api/pruebas.ts'
import { llaveDe, useCiclo } from './hooks/useCiclo.ts'
import { useEstado } from './hooks/useEstado.ts'

export default function App() {
  const banner = bannerPruebas()
  const { estado, fase, mensaje } = useEstado()
  const ciclo = useCiclo(llaveDe(estado))

  return (
    <div className="min-h-dvh bg-background text-foreground">
      {banner ? banner() : null}
      <Cabecera estado={estado} fase={fase} mensaje={mensaje} />
      <main className="mx-auto max-w-5xl px-6 py-8">
        {estado === null ? (
          <p className="text-sm text-muted-foreground">
            {fase === 'error'
              ? (mensaje ?? 'No se pudo contactar con el servidor.')
              : 'Esperando la primera respuesta del sistema…'}
          </p>
        ) : (
          <TarjetaSenal estado={estado} calidad={ciclo.datos?.calidad ?? null} />
        )}
      </main>
    </div>
  )
}
