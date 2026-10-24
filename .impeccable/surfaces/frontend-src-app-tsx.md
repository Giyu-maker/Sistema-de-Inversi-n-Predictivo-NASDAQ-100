---
version: 1
slug: "frontend-src-app-tsx"
primary_target: "frontend/src/App.tsx"
related_targets: []
---

# Dashboard de operación — Fase B2 (Sprint 3)

Pantalla única de operación del sistema RiTech-NASDAQ100, servida a la intranet. Modo **Operate**: producto de trabajo, el mundo solo aporta tipografía, paleta, densidad y un movimiento firma.

- **Audiencia:** Operador de Inversiones (la Secretaria/traders, SRS §2.2 — no es trader profesional) y, de vez en cuando, la Gerente General. M3, Windows 11, Edge (Chromium); jornada de monitoreo con consultas repetidas.
- **Trabajo:** responder en segundos: ¿está sano el sistema? · ¿hay señal operable y por qué? · ¿qué pasa con QQQ? Lenguaje llano en español («Sistema normal», «Atención: la IA no responde», «Mercado cerrado»), texto + icono siempre; jerga técnica (macro-F1) solo en el panel de detalle.
- **Prueba:** datos reales de la API de B1 (`127.0.0.1:8010`, proxy Vite; Nginx desde M2 en producción, mismo origen). Fixtures tipadas solo en desarrollo, solo estados que el sistema real no produce a demanda (señal operable, historial largo, extremos), con banner permanente «DATOS DE PRUEBA». Sin datos inventados en producción.
- **Alcance:** sin botón Kill-Switch (ni deshabilitado), sin login, sin reportes, sin IB: la cabecera queda estructuralmente extensible al Sprint 4. RNF-003 cierra por componentes (ciclo local B1 + latencia de rutas p50/p95 + render de cliente), con la latencia de polling (≤10 s) documentada aparte como compromiso de diseño. RNF-004 (carga < 1,5 s): pocas fuentes, pocos pesos, subconjunto.
- **Abierto:** nada bloqueante; tema con preferencia del sistema por defecto y conmutador claro/oscuro con localStorage (SRS §5.1).

## Direction contract

**THESIS:** El operador decide en segundos si hay señal y por qué. Se rehúsa el default de la categoría (dashboard financiero oscuro con velas neón y tarjetas KPI intermitentes): pantalla cálida y editorial donde el veredicto se lee en tipografía apacible, no en luces de alarma.

**OWN-WORLD:** Monocromo cálido: lienzo #FBFBFA, tinta #111111, filete 1px #EAEAEA, radio 8px, sin sombras. Color solo semántico con los pasteles del ancla, revisados en ambos temas para AA (verde/ámbar/rojo/azul). Tema: preferencia del sistema por defecto, conmutador con localStorage. Tipografía: Geist Sans (UI), Newsreader con `font-variant-numeric: tabular-nums lining-nums` (números-tesis), JetBrains Mono (datos, fechas `DD/MM/YYYY HH:mm:ss`); woff2 autoalojados con licencia OFL en el repo, subconjunto latino, pocos pesos. Iconos: Phosphor Bold (MIT). Formato es-CO con espacio fino antes de % («28 %»). Bento asimétrico, densidad de mesa de trabajo.

**STORY:** Entra y ve «Mercado cerrado» calmado con la última sesión y su fecha; la predicción de la sesión anterior (UP, confianza 0,28) con su porqué en una línea; entiende que no hay señal y por qué; debajo, velas de QQQ, rendimiento virtual e historial. Nunca se lee como error lo que es el estado normal.

**FIRST VIEWPORT:** Cabecera fija siempre visible: estado en lenguaje llano + icono, motivo, antigüedad del dato, franja de sesión (apertura/cierre 09:30–16:00 ET con posición actual; SOLO horario regular —CR-002—, sin ventanas de exclusión porque RF-204 no existe). Tarjeta de señal: veredicto prominente; **UP/DOWN** ⇒ confianza en serif grande sobre «la regla del umbral» (tick del umbral del perfil en la misma medida, distancia = veredicto, enmarcado solo al cruzar); **STABLE** ⇒ «Sin movimiento esperado» con su confianza, sin tick ni umbral; confianza baja en texto llano («tendencia con baja confianza», nunca «28 % de probabilidad»); XAI en 1–2 líneas con «ver detalle»; franja discreta de calidad del modelo pegada a la tarjeta, con panel desplegable (técnica aquí). Gráfico de velas 5M. Bajo el pliegue: rendimiento con su nota «virtual» e historial paginado con «cargar más».

**FORM:** Superficie única Operate dentro del ancla minimalist-ui (§17.2: una sola dirección visual — sin torneo de conceptos). Movimiento firma: «la regla del umbral», solo para UP/DOWN. Movimiento mínimo y funcional; `prefers-reduced-motion` respetado. Stack: React + Vite + Tailwind + shadcn/ui (componentes mínimos), TS estricto — fijados por SRS §5.1; **Recharts** para los gráficos proviene del backlog Ítem 8 (RF-05), no del SRS: única dependencia nueva de B2.4, versión fijada (3.10.1), cargada con `React.lazy` para no retrasar cabecera ni tarjeta. `seed: none (unavailable)` (dirección anclada por brief; concept-seed no corre).

**FINISH:** unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
