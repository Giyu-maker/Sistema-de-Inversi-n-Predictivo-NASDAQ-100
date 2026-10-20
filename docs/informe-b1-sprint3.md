# Informe de cierre — Sprint 3, Fase B1

| Campo | Valor |
|---|---|
| **Fecha** | 20/10/2026 |
| **Tag** | `sprint-3-b1` (anotado, apunta a `84a53a8`) |
| **Alcance** | API de solo lectura de M2, ciclo `scheduler → inferencia M1 → ai_predictions`, backend de M2 en Docker y benchmark de RNF-003 |
| **No cierra el Sprint 3** | El dashboard (Fase B2) sigue pendiente; RNF-003 queda provisional hasta medir la interfaz |
| **Estado** | Cerrado y verificado (635 pruebas en verde + verificación en vivo contra servicios reales) |

---

## 1. Commits (conciliación)

`git log --oneline sprint-3-fase-a..HEAD` devuelve **7 commits**: los 5 de esta Fase B1 y 2 previos de documentación que quedaron entre el tag de la Fase A y el inicio del código de B1. El reporte de sesión dijo «cuatro» en un momento y «cinco» en otro: el número correcto de commits de B1 es **cinco**.

| Commit | Contenido |
|---|---|
| `2ec0ee6` | Checkpoint de B1: API, ciclo, capa de base de datos, utils, `/modelo` |
| `4762cc2` | Pruebas de endpoints, ciclo y `GET /modelo` + dos correcciones que estas pruebas destaparon |
| `5c2158f` | Backend de M2 en Docker (`Dockerfile.backend`, `.dockerignore`, servicios `api`/`scheduler`) + arreglo de la siembra |
| `81a8a11` | `api` y `scheduler` en red host; el `.env` deja de sobrescribirse |
| `84a53a8` | Benchmark RNF-003 y esta documentación (CR-004, deuda D4, despliegue §11) |
| `1fb0a8f`, `577c7c1` | Previos a B1 (PRODUCT.md y herramientas de la Fase B, §17) — no son de esta fase |

---

## 2. Qué existía y qué se construyó

**Existía** (Fase A + Sprint 2): modelo PyTorch y `train`/`serve` en M1, features sin fuga de datos, XAI, IngestionEngine con cadencia de 5 min, `AsymmetricCalculator`, bases en Docker (solo `postgres` e `influxdb`) y la suite del Sprint 2.

**Se construyó en B1:**

- `src/endpoints/` — API de M2 con 7 rutas GET, errores en español y Pydantic estricto (`extra="forbid"`, RNF-011).
- `src/database/predictions.py` y `src/database/configuraciones.py` — repositorios de `ai_predictions` (insert-if-absent por `prediction_time`, D2) y `system_configurations` (siembra MODERATE 0,75, D3).
- `src/ai/prediccion.py` — el paso `ventana → inferencia → persistencia`, idempotente y a prueba de fallos (IA caída no persiste; ventana incompleta espera).
- `src/ai/serve.py` `GET /modelo` y `src/ai/client.py` `modelo()` (D5).
- `src/security/perfiles.py` — la ÚNICA regla de «señal operable» (D7), decidida en el backend.
- `Dockerfile.backend`, `.dockerignore` y los servicios `api`/`scheduler` del `docker-compose.yml`.
- `scripts/bench_rnf003.py` — el benchmark de esta fase.

---

## 3. Contrato de la API (7 rutas, todas GET)

Los nombres del SRS/SDD se mantienen literales (`trend_prediction`, `confidence_score`, `technical_rationale`); lo nuevo va en español. Todo es `extra="forbid"`: una respuesta con un campo inesperado rompe un test, no pasa desapercibida. Errores: `{"mensaje": "…"}` en 404/405/422/503, sin stack traces.

### `GET /api/health`
`{estado, servicio, version}` — vida del proceso; no toca PostgreSQL ni InfluxDB.

### `GET /api/status`
`{estado, motivo, mercado, ultima_vela, retraso_max_s, ia, modo, perfil, ultima_prediccion, senal, avisos}`

- `estado`: `VERDE` | `AMARILLO` | `CERRADO` (neutro, fuera de sesión NYSE).
- `mercado`: `{abierto, motivo, feriado, zona, horario}`.
- `ultima_vela`: `{timestamp, fecha, precio, antiguedad_s}` o null.
- `ia`: `{disponible, detalle}`; `modo`: `{nombre, descripcion, autonomo_disponible, motivo_bloqueo}`; `perfil`: `{nombre, umbral_confianza, max_loss_day, por_defecto}`.
- `senal`: ver `/api/predictions/latest`. `avisos`: cadenas en español (configuración por defecto, Influx caído…).

### `GET /api/candles?limit=&start=&stop=&aggregate=`
`{velas, total, aggregate, nota}`; cada vela: `{timestamp, fecha, open, high, low, close, volume}` con `fecha` en `DD/MM/YYYY HH:mm:ss` (hora de Colombia). `aggregate`: `5m` | `15m` | `1h` | `1d`, resuelto **en Flux**. `start` y `stop` van juntos (422 si solo uno); `limit` 1–5000.

### `GET /api/predictions?limit=&offset=`
`{predicciones, total, limit, offset, avisos}`; orden descendente; **una sola consulta a InfluxDB por página** para el resultado real. Cada predicción: `{id, prediction_time, fecha, trend_prediction, confidence_score, technical_rationale, resultado_real}`, y `resultado_real`: `{estado, motivo, retorno_pct, etiqueta, banda_pct}` con `estado` ∈ `DISPONIBLE` | `PENDIENTE` | `NO_APLICA` (o null si InfluxDB no responde: el historial se sirve igual y el aviso lo dice).

### `GET /api/predictions/latest`
`{encontrada, prediccion, senal, avisos}`; `senal`: `{operable, motivo, umbral, perfil, tendencia, confianza}` — el veredicto lo decide el backend con el umbral del perfil (D7), la interfaz solo lo pinta.

### `GET /api/yield`
`{virtual, nota, disponible, motivo, apertura_sesion, ultima_vela, variacion_nasdaq_pct, rendimiento_ritech_pct, limite_perdida_pct, umbral_perdida_alcanzado}`. Siempre `virtual: true` con su nota («sin capital»): es RF-201 sobre la serie, no una orden.

### `GET /api/model-quality`
`{disponible, motivo, model_type, feature_schema_version, entrenado_en_ms, entrenado_en, dispositivo_entrenamiento, macro_f1_test, banda_stable_pct, acierto_direccional, nota}`; `acierto_direccional`: `{ratio, n, aciertos, ic_95, fuente, ventaja_demostrada}`. Con M1 caído **no es error HTTP**: 200 con `disponible=false` y su `motivo`.

---

## 4. Cifras de RNF-003 (`scripts/bench_rnf003.py`, 20/10/2026)

50 iteraciones contra InfluxDB, PostgreSQL y el servicio de M1 **reales**. El texto del SRS acota el requisito «en el servidor local», así que la traída a Twelve Data se mide y se informa **aparte** (decisión que el backlog dejó abierta desde el Sprint 2).

| Fase | n | p50 (ms) | p95 (ms) | máx (ms) |
|---|---:|---:|---:|---:|
| Traer vela de Twelve Data (red externa) | 1 | 446,36 | 446,36 | 446,36 |
| Validar payload (RF-101) | 50 | 0,35 | 0,64 | 0,74 |
| Escribir vela en InfluxDB (con guarda CR-003) | 50 | 11,77 | 37,70 | 45,19 |
| Leer la ventana de InfluxDB | 50 | 62,21 | 72,56 | 77,44 |
| Extraer features de la ventana | 50 | 0,52 | 0,87 | 0,99 |
| Inferencia en M1 (HTTP `/predict`) | 50 | 6,32 | 7,89 | 8,38 |
| Cálculo asimétrico (RF-201/202) | 50 | 0,01 | 0,02 | 0,02 |
| Persistir predicción en PostgreSQL | 50 | 1,16 | 1,64 | 2,71 |
| **TOTAL ciclo local (backend)** | **50** | **84,65** | **117,32** | **122,90** |

**Veredicto: CUMPLE** los 500 ms del servidor local (p95 117,32 ms), **PROVISIONAL**:

- Falta la **5.ª componente** de RNF-003 («actualizar la interfaz»), que existe solo desde la Fase B2: ahí se mide y se cierra el requisito.
- La inferencia corrió en **CPU** (desarrollo en M2); en M1 con la GTX 1660 SUPER se vuelve a medir al desplegar.
- La traída externa (446 ms, muestra única; el plan Basic cuesta 1 crédito por petición) no se suma al presupuesto.

Limpieza del benchmark, verificada: PostgreSQL sobre una tabla temporal `LIKE ai_predictions` con `ROLLBACK` por iteración (0 filas en la tabla real) y velas sintéticas de 2001 en InfluxDB borradas al terminar (0 puntos residuales; el histórico de octubre quedó intacto, 1 092 velas).

---

## 5. D5 — nota de calidad del modelo (`/modelo` + `/api/model-quality`)

**Estado: implementado, probado y verificado en vivo.**

- `GET /modelo` (M1): expone del artefacto `model_type`, `feature_schema_version`, ventana (12), horizonte (3), banda (0,10 %), fecha y dispositivo de entrenamiento y `macro_f1_test`. El acierto direccional sale del informe `.metrics.json` del entrenamiento y, si no existe, se **deriva de la matriz de confusión** sin reentrenar; sin ninguna fuente: `fuente: "no_disponible"` y ratio null — nunca un número inventado. 7 pruebas.
- `GET /api/model-quality` (M2): la misma nota para la interfaz, con M1 caído respondiendo `disponible=false` en lugar de un error HTTP. 4 pruebas.
- Verificado en vivo (20/10/2026): `macro_f1_test 0,4313`; acierto direccional **532/1125 = 0,4729** (`fuente: informe_entrenamiento`); `ventaja_demostrada: false` porque el IC 95 [0,4387; 0,5078] contiene el 50 %; la nota recurre a «sin ventaja direccional» y dice explícitamente que el endpoint no emite órdenes de compra ni de venta.

---

## 6. CR-004 — estado

**Registrado** en `docs/change-requests.md` (20/10/2026), **pendiente de aprobación formal**. Registra dos adiciones que el SRS/SDD v1.0 no preveían y una brecha declarada:

1. La superficie de la API de M2: **SOLO GET, sin autenticación ni roles**, con la mitigación de esta fase (`127.0.0.1`, POST contesta 405, sin secretos en respuestas) y el **cierre en el Sprint 4** junto con `trader_users` y permisos por rol.
2. El estado **`CERRADO`** neutro fuera de sesión NYSE (SRS §10.3 solo prevé verde/amarillo/rojo; el rojo es exclusivo del Kill-Switch).

Enlaza con la **deuda D4** del backlog (el API usa el mismo usuario de PostgreSQL que el resto: sin rol de solo lectura), también con cierre en el Sprint 4.

---

## 7. Verificación de entorno (tras el incidente de pérdida de archivos)

Los archivos ignorados por git existen y funcionan:

| Archivo | Estado |
|---|---|
| `.env` | OK (lo consumen tests, benchmark y compose) |
| `.env.local` | OK (`CREDENTIALS_MASTER_KEY`, `INFERENCE_URL`) |
| `models/` | OK: 4 artefactos `.pt`; `active.json` con `path` → `ritech-mlp-20261018T021631Z.pt`, que existe |
| `.venv/` | OK: importa `torch`, `fastapi`, `psycopg2`, `influxdb_client`, `httpx`, `numpy` |

Arranque desde cero verificado hoy: `docker compose down` → `docker compose up -d --build` → los cuatro contenedores levantan (`api` healthy) y **las 7 rutas responden 200** con datos reales (`CERRADO` en `/api/status`, predicción `561b6e22…` en `/api/predictions/latest`, QQQ en `/api/candles`, rendimiento virtual en `/api/yield`, nota de calidad en `/api/model-quality`).

Ciclo en vivo: el scheduler siembra la configuración (`Modo Asesor, MODERATE 0,75, 1,25 %`), persiste la vela sin tocar el histórico (guarda CR-003) y cerró `scheduler → M1 → ai_predictions` con una predicción real (UP, confianza 0,28 → no operable frente al umbral 0,75). La rama de fallo también se vio en vivo: con M1 inalcanzable el ciclo registró el error y no persistió nada.

---

## 8. Bugs reales que esta fase destapó (todos corregidos con prueba de regresión)

1. **`GET /modelo` devolvía 500 en cada llamada**: leía `modelo.metrics` y `modelo.horizon`, que no existen en `LocalPredictiveModel`; los datos viven en el artefacto. Cazado por las pruebas nuevas del contrato.
2. **La siembra nunca corría en un arranque real**: `main()` del scheduler llamaba `seembrar_inicial` (typo). Lo descubrió el primer arranque en contenedor; el doble estricto del test (un `Mock` habría dejado pasar el typo) verifica ahora ambos sentidos.
3. **PostgreSQL caído no era causa de amarillo** en `/api/status` (solo aviso): responder VERDE sin la tabla sería mentir.
4. **El firewall del host bloquea el tráfico del puente docker→host** (connect timeout desde el contenedor hacia un servicio del host): origen de un timeout de inferencia. Resuelto con `network_mode: host`, que además es la topología real de M2 y deja el `.env` sin sobrescribir.
5. **El API de borrado de InfluxDB recusa enteros**: `start`/`stop` en RFC3339 (en el benchmark).

---

## 9. Qué queda

- **Fase B2:** dashboard React (impeccable + minimalist-ui, shadcn/ui, `break-ui` por pantalla), y con él el cierre de RNF-003 midiendo la actualización de la interfaz. El tag de cierre del Sprint 3 (`sprint-3-cierre`) se pone solo con confirmación explícita.
- **Sprint 4:** login y roles (cierre de CR-004 y deuda D4), Kill-Switch, auditoría, notificaciones e IB.
