# Informe B2 — Fase del dashboard, Sprint 3 (RiTech-NASDAQ100)

| | |
|---|---|
| **Fecha** | 25/10/2026 |
| **Alcance** | Fase B2 completa: dashboard de operación (B2.1–B2.4) + cierre B2.5 (Nginx, recuperación de huecos, break-ui, RNF-003/004, revisión de capturas P1/P2, stack listo para el lunes) |
| **Estado** | Cerrada a nivel de código y mediciones; **el tag `sprint-3-cierre` NO se crea**: lo confirma la Gerente/Gerente General tras revisar este informe |
| **Verificación** | Backend **668 pruebas** en verde · frontend **33** en verde · oxlint 0 · `tsc`/`vite build` OK · stack Docker (5 contenedores) healthy con el código actual |

---

## 1. Construcción (por commits)

| Ítem | Commit | Qué quedó |
|---|---|---|
| B2.1 scaffold | `4b5f8f8` | Panel de operación, contrato de dirección |
| B2.2 datos | `cb662b4` | Cliente HTTP tipado con Zod (tolerante a campos nuevos), latido con backoff y encadenado, fixtures de desarrollo |
| B2.3 cabecera + tarjeta | `7e99dd5`, `1dd2095` | Estado en vivo, franja de sesión, tema, tarjeta de señal, textos en lenguaje llano |
| B2.4 gráfico/rendimiento/historial | `7c12a2a` (tag `sprint-3-b2`) | Velas 5M con Recharts (`React.lazy`), rendimiento asimétrico, historial paginado |
| B2.5.1 Nginx | `c573a55` | `Dockerfile.web` multi-etapa, proxy `/api` en mismo origen, gzip + caché (assets hasheados `immutable`, `index.html` `no-store`), `WEB_BIND` opt-in LAN, RNF-004 contra Nginx |
| B2.5.2 huecos + restart | `b86d3cb`, `ea1ab4f` | Recuperación por sesión candidata (78 slots vs Influx), **huecos internos**, última sesión cerrada, cuota 8/min·800/día, `restart: unless-stopped` en los 5 servicios |
| B2.5.3 break-ui | `e4ffaf0` | 4 escenarios nuevos (`sin-velas`, `una-vela`, `dato-desactualizado`, `api-caida` con `RED_CAIDA`), 19 capturas, 3 hallazgos críticos corregidos (aria-label de la franja, rendimiento vs gráfico, textos largos a 390 px) |
| Instrumentación RNF-003 (c) | `745a9d8` | `performance.mark/measure` en `useEstado`/`useCiclo` (datos validados → doble rAF → pintado) |
| Reglas de agente | `bcbd8cd` | AGENTS.md §18: procesos en 2.º plano con log/PID |
| Prioridad del lunes (4 puntos) | `ea1ab4f`, `9f40af8`, `f2d3611` | Huecos internos/cerrada, `scripts/sesion.sh start\|stop\|estado` (inferencia en la IP del puente, nunca 0.0.0.0), verificación en frío completa y §11.1 de `docs/despliegue.md` (lunes en 5 pasos) |
| **Revisión de capturas P1** (10/10) | `132f23c` | Titular=veredicto; dirección sin colores de estado; leyenda con las formas de las marcas; hora de Colombia + ET en la última vela; «Última predicción disponible»; QQQ no es el índice; «Mercado inestable» vs «Revisar el sistema» (íconos distintos); textos backend en porcentajes/perfil español/CPU; **puerta de sesión 09:30–16:10 ET (~288 créditos/día antes)** con feriados NYSE mínimos; confianza nunca «100,0 %» |
| **Revisión de capturas P2** (6/6) | `d86c29c` | Estado `SIN_DATOS` («Sin datos para evaluar») + acierto/falla con ícono; motivos de cierre **calculados** con la fecha real; XAI con la conclusión primero; gráfico de línea <480 px y filas de dos líneas fijas; fixtures con «ahora» coherente; marcador dentro de la pista |
| **Punto 3 — fecha de sesión en un solo lugar** | `5d40b07` | `apertura_ms`/`etiqueta_ny`/`ahora_ny`/`ventana_de_consulta` viven en `src/utils/mercado.py` (America/New_York siempre; Bogotá solo muestra); `huecos`, `backfill` y `engine` consumen de ahí; tests de invierno EST y del cruce 30/10→02/11 |
| **Punto 4 — este informe** | (este commit) | `scripts/bench_rnf003_web.mjs`, `scripts/bench_rnf003_ui.mjs` y cifras de RNF-003/RNF-004 |

Estado del stack al cierre: `docker compose up -d` con imágenes reconstruidas (api, scheduler, web + postgres + influx) — **listo para la sesión del lunes 26/10 siguiendo `docs/despliegue.md` §11.1**. El scheduler ya no consulta la API fuera de la ventana (ahorro de ~288 créditos/día del plan Basic de 800).

---

## 2. Cifras — RNF-003 (SRS §6.2: «…no debe superar los **500 ms** en el servidor local»)

**Tabla única por componentes** (medido el 20/10 y 25/10/2026; máquina M2):

| Componente | Medición (script) | n | p50 (ms) | p95 (ms) | Presupuesto |
|---|---|---:|---:|---:|---|
| **(a) Ciclo local** — validar → escribir Influx → leer ventana → features → inferencia → asimetría → persistir PG (`scripts/bench_rnf003.py`, servicios reales, B1) | backend | 50 | **84,65** | **117,32** | < 500 ms |
| **(b) Rutas de la UI contra Nginx** — 7 rutas × 50, secuencial (`scripts/bench_rnf003_web.mjs`, 127.0.0.1:8080) | HTTP+proxy | 350 | **60,58** | **112,86** | — (parte de la actualización de UI) |
| **(c) Render del cliente tras datos** — `performance.measure` en producción (`scripts/bench_rnf003_ui.mjs`) · latido de estado | navegador | 7 | **29,70** | **39,40** | — |
| **(c) Render del ciclo completo** (velas+predicción+rendimiento+calidad) | navegador | 1 | 52,60 | 52,60 | — |
| **Suma de peores casos (a)+(b)+(c-estado)** | | | | **269,58** | **< 500 ms** ✓ |

Detalle de (b): `/api/yield` es la más lenta (p50 110,32 / p95 122,31 ms — lee Influx); `/api/model-quality` y `/api/health` ≤ 3 ms; el resto p50 58–67 ms.

**Compromiso documentado aparte (decisión D8): el polling ≤ 10 s.** La interfaz se actualiza con el latido de `/api/status` cada 10 s; una vela nueva puede tardar hasta 10 s en reflejarse en pantalla. Ese retardo **no entra en el cronómetro de 500 ms** (el SRS mide «en el servidor local», de vela a cálculo), pero sí afecta al tiempo percibido de «actualizar la interfaz» en la práctica.

**Veredicto: NO se declara CUMPLE completo.** Los cinco componentes del requisito están medidos y caben de sobra (peor suma 269,58 ms < 500 ms), pero faltan dos cosas para el cierre total:
1. La **observación end-to-end con una vela real** (B2.5.5): hoy es fin de semana y el mercado no abre — se observa el lunes 26/10 a partir de las 08:30 (hora Colombia).
2. La muestra de (c)-ciclo es **n=1** (sin velas nuevas no hay ciclos que renderizar); con mercado abierto se repite la medición.

Con esas dos evidencias el requisito puede declararse cumplido sin matices; hoy queda **CUMPLE POR COMPONENTES, PENDIENTE DE CONFIRMACIÓN END-TO-END**.

### RNF-004 («…cargada a través de la intranet local … en menos de 1.5 segundos»)

`node scripts/bench_rnf004.mjs http://127.0.0.1:8080/ 5` — build de producción servido por Nginx, sin vendor, 5 corridas (25/10/2026):

| Componente | Mediana | Objetivo | |
|---|---:|---:|---|
| Carga completa (`loadEventEnd`) | **28 ms** | 1500 ms | **CUMPLE** ✓ |
| Primer pintado (FCP) | 68 ms | — | |
| Primera respuesta real de `/api/status` | 114 ms | — | |
| Chunk diferido del gráfico (Recharts) | 129 ms | — | |

Mínimos por corrida: 27–42 ms de carga completa. Margen de sobra (53× por debajo del objetivo).

---

## 3. Capturas y revisión

- **`docs/capturas-b2/`** (gitignored): 16 capturas de B2.4 (1440×900 y 390×844, claro/oscuro) + **19 de break-ui** (`break-<escenario>-…`: los 6 escenarios × claro/oscuro/móvil + zoom 200 %), con informe automatizado: 0 errores de consola, 0 desborde de página en todas las vistas, teclado sin trampas y con foco visible.
- **`docs/revision-capturas-b2.md`**: la revisión de la Gerencia — **P1 10/10 implementados** (`132f23c`), **P2 6/6** (`d86c29c`), **P3 0/2** (ver brechas).
- Hallazgos de break-ui corregidos en `e4ffaf0`: aria-label de la franja que mentía tras el cierre; cifras de rendimiento que no cuadraban con el gráfico; textos largos del backend que se salían de la tarjeta a 390 px.

---

## 4. Brechas conocidas (lo que queda fuera de este cierre)

1. **P3.1** — Valores pequeños con 3 decimales («+0,03 %» parece error de redondeo). Pendiente por tiempo.
2. **P3.2** — Mostrar las probabilidades de las tres clases dentro de «Ver detalle» si `/api/predictions/latest` las trae (hoy no: cambiar el contrato ahora es prematuro; anotado como mejora).
3. **RNF-003 end-to-end** — observación con vela real: **lunes 26/10** (ver §2).
4. **Render del ciclo con n=1** — se re-mide con mercado abierto.
5. **Inferencia en CPU** — las cifras de (a) corrieron en M2; en M1 (GTX 1660 SUPER) se re-mide al desplegar (igual que en B1).
6. **Sin login/roles/Kill-Switch/IB**: fuera del Sprint 3 por definición (Sprint 4).
7. La medición de (b) es **sin concurrencia** (secuencial a propósito); una sesión real con varias peticiones solapadas no está medida.

## 5. Desviaciones (y por qué)

1. **Nuevo estado `SIN_DATOS` en `resultado_real`** (P2.1): la decisión D6 hablaba de tres resultados; «No aplica» engañaba separando «horizonte fuera de sesión» de «faltan velas». Es una ampliación del contrato de la API (campo `estado` es string en el esquema, compatible hacia atrás); convendría registrarla en `docs/change-requests.md` en la revisión formal.
2. **Fixtures = réplicas del backend, no fuente**: los textos de `src/fixtures/` imitan los de Python (perfiles, estado, XAI). Si cambia el backend, cambian los fixtures — así está hecho y con test (`esquemas.test.mjs` exige el copy literal).
3. **`extremos` inyecta su rendimiento (−2,14 %, caso de gap)**: es el único cifra que no sale de la serie sintética (a propósito: el escenario existe para ver el gap). Documentado en el propio código.
4. **Casi-desvío de zona horaria** (incidente del 25/10): se intentó corregir la detección de huecos confundiendo el `fecha` que la API formatea en **hora Colombia** (regla SRS) con la hora ET del `timestamp`. El cambio se revirtió y quedó la regresión `test_sesion_completa_en_verano_no_pide`; costó 1 crédito inútil de los 800 diarios. La lección está escrita en el test: **comparar contra los timestamps, nunca contra el texto**.
5. **La API se consultaba fuera de sesión** (~288 créditos/día): corregido con la puerta de sesión (P1.9); la recuperación de huecos corre igual al arranque y una vez tras el cierre.

---

## 6. Cómo se corrieron las cifras

```bash
# (a) — B1, servicios reales (informe-b1-sprint3.md §4)
set -a; source .env; source .env.local; set +a
.venv/bin/python scripts/bench_rnf003.py            # 50 iteraciones

# (b) — rutas contra Nginx
node scripts/bench_rnf003_web.mjs                   # 7 rutas × 50

# (c) — render del cliente (build actual servido por Nginx)
node scripts/bench_rnf003_ui.mjs http://127.0.0.1:8080/ 70

# RNF-004 — carga inicial contra Nginx
node scripts/bench_rnf004.mjs http://127.0.0.1:8080/ 5
```
