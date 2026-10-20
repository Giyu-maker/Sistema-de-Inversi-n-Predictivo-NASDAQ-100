# Frontend — Panel de operación RiTech NASDAQ-100

Dashboard de la **Fase B2 del Sprint 3**: React + Vite + TypeScript estricto + Tailwind CSS v4 + shadcn/ui + Recharts, tal como fija el SRS/SDD. Interfaz en español neutro; contratos y dirección visual en `PRODUCT.md` y `.impeccable/surfaces/frontend-src-app-tsx.md`.

## Comandos

```bash
npm install      # una vez
npm run dev      # 127.0.0.1:5173 — /api en proxy hacia la API de M2 (127.0.0.1:8010)
npm run build    # tsc -b (strict) + vite build
npm run lint     # oxlint
```

## Convenciones

- **Tipado estricto** (`strict: true`); `any` prohibido (RNF-011).
- **Fuentes autoalojadas** vía fontsource (Geist, Newsreader, JetBrains Mono): sin CDN, subconjunto por `unicode-range`, pocas variables. Licencias OFL en `LICENCIAS/`.
- **Tema**: preferencia del sistema por defecto; conmutador con `localStorage('tema')` (SRS §5.1). Inicialización previa al pintado en `public/tema.js`.
- **Formato**: fechas `DD/MM/YYYY HH:mm:ss`; cifras con espacio fino antes del % («28 %»); números-tesis en `.cifra` (tabulares + lining) y datos en `.dato` (mono).
- **Estados** verde/ámbar/rojo/azul: tokens `*-fondo` / `*-texto` (AA), siempre acompañados de texto + icono.
