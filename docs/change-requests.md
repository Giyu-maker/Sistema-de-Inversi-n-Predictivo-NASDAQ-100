# Registro de Solicitudes de Cambio (Change Requests) — RiTech-NASDAQ100

Documento de trazabilidad de cambios a los artefactos formales aprobados (SRS v1.0, SDD v1.0). Cada entrada sigue el mismo mecanismo: un hallazgo detectado durante la implementación, su impacto en los documentos aprobados, y una decisión explícita en vez de un parche silencioso en el código.

---

## CR-001 — Campos `price_high` / `price_low` faltantes en la entidad `NasdaqTick`

| Campo | Valor |
|---|---|
| **Fecha de detección** | Sprint 2 — implementación de `IngestionEngine` |
| **Solicitante** | Equipo de desarrollo (Naranja's Team) |
| **Documentos afectados** | SRS v1.0 §4.2.2 (Diccionario de Datos); SDD v1.0 §5.1 (firma de `write_to_influx`) |
| **Tipo** | Corrección / clarificación de requisito |
| **Prioridad** | Media |
| **Estado** | **Aprobada — 17/10/2026, por el equipo de desarrollo (Naranja's Team)** |

### Hallazgo
- SRS §3.1.2 (narrativa de la Feature 1) establece que "la API devuelve el precio de apertura, cierre, **máximo, mínimo** y volumen de la vela de 5 minutos".
- SRS §4.2.2 (Diccionario de Datos, entidad `NasdaqTick`) y SDD §5.1 (firma de `write_to_influx`) solo contemplan `price_open`, `price_close` y `volume` — **sin** `price_high` / `price_low`.
- Es una inconsistencia real entre la narrativa del documento y sus propios artefactos formales (diccionario de datos y firma de clase), no una omisión introducida por el equipo de desarrollo.

### Cambio aprobado (CR-001)
- Agregar `price_high: Float` y `price_low: Float` a la entidad `NasdaqTick` en SRS §4.2.2. **(Hecho — SRS v1.1)**
- Actualizar la firma de `write_to_influx()` en SDD §5.1 para incluir ambos parámetros, además de documentar la entidad `NasdaqTick` en SDD §3.1.2. **(Hecho — SDD v1.1)**
- Ambas documentos reciben fila nueva en su historial de revisiones (v1.1, referencia a esta CR).

### Justificación
Estos valores son necesarios como features de entrada para el modelo predictivo de IA (Feature 3, RF-301 a RF-304) planificado para Sprint 3. Omitirlos ahora obligaría a rediseñar el esquema de InfluxDB más adelante, con el costo de reprocesar o perder histórico ya capturado.

**Confirmada durante el Sprint 3:** el dataset del modelo requiere el rango de la vela para las features de rango (rango relativo, posición del cierre dentro del rango y volatilidad por rango). Sin `price_high`/`price_low` persistidos esas features no existen, y el resto del histórico habría que reconstruirlo igualmente.

### Impacto
Bajo en diseño (cambio aditivo sobre dos documentos aprobados), **medio en datos**: exige reconstruir el bucket de InfluxDB, ver "Migración de datos" abajo.

### Estado de implementación
- `NasdaqCandle` ya validaba y capturaba ambos campos desde el Sprint 2; lo que faltaba era **persistirlos**.
- **17/10/2026 (Sprint 3):** con la CR aprobada, `write_to_influx()` acepta `high_p` y `low_p` en orden OHLC y los escribe; se retira el `TODO(SRS-4.2.2)` del código y se actualizan los tests.
- La firma pública pasa de literal-a-SDD-v1.0 a **literal-a-SDD-v1.1**.

### Migración de datos (requisito previo a ampliar el backfill)

La CR-003 añadió una guarda *insert-if-absent*: **no reescribe un timestamp que ya existe**. Consecuencia directa sobre las 10.000 velas ya ingeridas: la guarda impediría añadirles `price_high`/`price_low`, dejando el bucket con dos esquemas distintos (velas del Sprint 2 a 3 campos, velas nuevas a 5 campos) y sin poder calcular las features de rango sobre el histórico.

Por eso la adición de los campos **no puede ser incremental** y requiere una reconstrucción única:

| Aspecto | Detalle |
|---|---|
| Herramienta | `python -m src.ingestion.rebuild --confirm-wipe` |
| Confirmación | **Exige el flag `--confirm-wipe`.** Sin él, el script se niega y no toca nada |
| Alcance | Borra los puntos `NasdaqTick` del bucket y vuelve a ingerir todo desde Twelve Data con la firma de 5 campos |
| **¿Es una escritura destructiva del pipeline?** | **No.** Nunca se invoca desde el orquestador, el backfill ni ningún proceso automático: es una **migración manual, única y explícita**, previa a tener datos de producción reproducibles |
| Reproducibilidad | Los datos se reobtienen de la misma fuente (`Twelve Data`, QQQ, 5 min) y se verifican por conteo y por rango de fechas tras la reconstrucción |
| Relación con CR-003 | **No contradice el SRS §4.4.** La inmutabilidad protege el histórico **del pipeline en operación**; esta operación la realiza un administrador de forma consciente y documentada, antes de declarar los datos como definitivos |

> **Nota de secuencia:** la migración se ejecuta **antes** de ampliar el backfill a 2 años, para reconstruir el bucket **una sola vez** y no dos.

**Estado de la migración:** ✅ **Ejecutada el 17/10/2026** con `rebuild --confirm-wipe`.

| Fase | Resultado |
|---|---|
| Estado previo | 10.000 puntos con **3 campos** (`price_open`, `price_close`, `volume`); `price_high`/`price_low` **ausentes** |
| Borrado | `HTTP 204`, verificado: **0 puntos** `NasdaqTick` residuales |
| Re-ingreso | `BACKFILL_DAYS=500` → **8 peticiones** de 5000, throttle 7.6 s, **69 s** en total |
| Resultado | **40.000 puntos con los 5 campos** (`price_open`, `price_high`, `price_low`, `price_close`, `volume` — verificado por consulta de campos distintos y por conteo independiente de `price_high`) |
| Rango | **2024-09-26 → 2026-10-16** (≈ 2 años) |
| Sesiones | **516** totales, **509 completas** (78 velas), 7 incompletas (borde inferior/superior) |
| Primera ejecución fallida | El predicado `_measurement == "NasdaqTick"` devolvió **400 "bad tag value"** (la API *delete* exige sintaxis `tag=valor`, no `==` de Flux). **No borró nada** — verificado por conteo antes de reintentar. Corregido a `_measurement=NasdaqTick` y protegido con test de regresión |

**Ningún dato se perdió de forma irreversible:** todo se reobtuvo de la misma fuente (Twelve Data, QQQ, velas de 5 min) y se verificó por conteo y por rango antes/después.

---

## CR-002 — Cambio de proveedor de datos de mercado: Alpha Vantage → Twelve Data

| Campo | Valor |
|---|---|
| **Fecha de detección** | 06/10/2026 — verificación con API real durante Sprint 2 |
| **Solicitante** | Equipo de desarrollo (Naranja's Team) |
| **Documentos afectados** | SRS v1.0 — RF-101, §2.5 (Suposiciones y Dependencias), §4.4 (Adquisición de Datos), §5.2 (Interfaces de Software); SDD v1.0 — referencias a Alpha Vantage en `IngestionEngine` |
| **Tipo** | Cambio de dependencia externa / requisito de infraestructura |
| **Prioridad** | Alta (bloqueaba la verificación de RF-101) |
| **Estado** | Implementado en código; pendiente de aprobación formal del documento |

### Hallazgo
Alpha Vantage cambió su modelo de negocio después de que el SRS fue aprobado (28/08/2026):

| Prueba (06/10/2026, con key real) | Resultado |
|---|---|
| `TIME_SERIES_INTRADAY` 5min (QQQ e IBM, `entitlement=delayed` y `realtime`) | `"This is a premium endpoint. You may subscribe..."` |
| Símbolos de índice (`NDX`, `^NDX`, `IXIC`) | `{}` vacío — Index Data APIs ahora son premium |
| `TIME_SERIES_DAILY` / `GLOBAL_QUOTE` | Funcionan en capa gratuita |
| Cuota gratuita | 25 peticiones/día, ~25s entre llamadas |

Dos hechos independientes del endpoint específico:
1. RF-101 exige muestreo cada 5 min, 4:00 AM–8:00 PM EST ≈ 192 peticiones/día (≈78 incluso limitándose a horario regular). La cuota gratuita de 25/día es estructuralmente insuficiente, sin importar qué endpoint se use.
2. El intradía de 5 min y los datos de índice ahora son exclusivos del plan Premium ($49.99/mes uso personal; uso comercial requiere contacto comercial, planes desde $499/mes).

### Alternativas evaluadas
| Fuente | ¿Velas 5M gratis? | Notas |
|---|---|---|
| **Twelve Data Basic** | ✅ 800 créditos/día, 8/min | Licencia "Individual — personal, internal, non-commercial" |
| Yahoo (no oficial) | ✅ (79 velas verificadas) | Sin SLA, sin clave, ToS gris — no apto para dependencia de pipeline |
| Polygon Basic | ❌ solo EOD gratis | Intradía desde $29/mes (retrasado 15 min) |
| Stooq | ❌ | Bloqueado por challenge JavaScript |
| Alpha Vantage Premium | ✅ | $49.99/mes personal, ~$499/mes comercial |

Se adoptó **Twelve Data Basic**.

### Verificación empírica del símbolo (06/10/2026)
| Prueba | Resultado |
|---|---|
| `NDX` (índice directo) | HTTP 404 — reservado a plan Grow+ |
| `prepost=true` (horario extendido 4:00–20:00 ET) | HTTP 403 — reservado a plan Pro+ |
| `QQQ` sin `prepost` | HTTP 200 — 200 velas de horario regular (09:30–15:55 ET) verificadas |

→ `SYMBOL = "QQQ"` (ETF proxy del NASDAQ-100) confirmado, ya que ningún plan gratuito evaluado expone el índice NDX crudo.

### Cambio propuesto
Reemplazar "Alpha Vantage" por "Twelve Data" en RF-101, §2.5, §4.4 y §5.2 del SRS; documentar `QQQ` como símbolo proxy del índice en lugar de NDX.

### Limitación conocida — no se resuelve con este cambio
**El plan Basic gratuito de Twelve Data no cubre horario extendido.** RF-102 (Prioridad **Alta**: "debe monitorear pre-market, horario regular y after-hours") queda **funcionalmente incumplido** bajo este plan — solo se captura horario regular (~78 de las ~192 velas/día del escenario original del SRS). Cerrar esta brecha requeriría el plan Pro+ de pago. Esto se deja como alcance conocido de Sprint 2/3, no como un defecto oculto.

### Nota sobre REST-03
REST-03 define el presupuesto ($20.000.000 COP) para "desarrollo e infraestructura física" como partida única; no contempla una suscripción recurrente de datos de mercado. Esta CR no solicita presupuesto porque el proyecto opera en contexto académico sin erogación real — pero deja constancia de que, en un despliegue real, esta sería una brecha de alcance en REST-03: el documento no define un presupuesto operativo (OpEx) separado del presupuesto de desarrollo (CapEx).

### Riesgo de licencia aceptado para este proyecto
Twelve Data Basic tiene licencia "Individual — personal, internal, non-commercial". Dado que este es un ejercicio académico sin actividad comercial real, el término es compatible con el uso actual. **Un despliegue real de producción requeriría migrar a un plan comercial licenciado.**

### Estado de implementación
- `TwelveDataProvider` implementado en `src/ingestion/providers/twelve_data.py`, detrás de la interfaz `MarketDataProvider` (intercambiable, conforme a SDD §6.1 — "cambiar de proveedor de datos solo requiere modificar `IngestionEngine`").
- `SyntheticMarketDataProvider` se mantiene para pruebas unitarias (no depende de la API real).
- Smoke test end-to-end ejecutado 06/10/2026 contra la API real: `fetch` real → `validate_payload` real → `write_to_influx` mockeado (primera pasada, sin Docker) → `AsymmetricCalculator` real, confirmando RF-201 (rendimiento = 2× variación positiva) contra datos de mercado reales.
- **06/10/2026 — Máquina 2 real (Docker 29.8.2 / compose 5.6.0):** smoke repetido con `write_to_influx` **real** contra InfluxDB 2.7 levantado por `docker compose up -d`: lectura de vuelta desde Flux confirma la vela persistida; verificación empírica de inmutabilidad y cronómetro RNF-003 en `docs/checklist_m2.md` §5 y §7. PostgreSQL 16 healthcheck `healthy` (sin escrituras aún — Sprint 3/4).
- Key temporal de Alpha Vantage destruida; el proveedor Alpha Vantage no vuelve a usarse en el código.

### Decisión requerida
Aprobación formal de: (1) el cambio de "Alpha Vantage" a "Twelve Data" en los documentos listados arriba, y (2) la aceptación explícita de que RF-102 queda parcialmente cubierto (solo horario regular) bajo el plan gratuito actual, como alcance conocido hasta que se evalúe un upgrade de plan.

---

## CR-003 — Inmutabilidad del histórico en InfluxDB: garantía de aplicación, no del motor

| Campo | Valor |
|---|---|
| **Fecha de detección** | Sprint 2 — cierre, verificación empírica de RF-103 |
| **Solicitante** | Equipo de desarrollo (Naranja's Team) |
| **Documentos afectados** | SRS v1.0 §4.4 (Integridad de Datos); SDD v1.0 §5.1 (firma de `write_to_influx`) |
| **Tipo** | Corrección de implementación / clarificación de requisito |
| **Prioridad** | Alta (RF-103, Prioridad Alta en el SRS) |
| **Estado** | Implementado y verificado en vivo; pendiente de aprobación formal |

### Hallazgo

SRS §4.4 (Integridad) exige textualmente:

> "InfluxDB almacena datos inmutables donde **no se permiten escrituras destructivas o modificaciones de registros de precios históricos**."

El equipo asumió que InfluxDB garantizaba eso por sí mismo. **No es así.** Verificación empírica del 06/10/2026 sobre InfluxDB 2.7 real (contenedor `ritech-influxdb27`): reescribir un punto con el mismo timestamp pero con un `volume` alterado **no lanzó error, no creó un duplicado y no dejó rastro** — el punto quedó con el valor nuevo (*last write wins*). Es decir, el motor sobrescribe el histórico de forma **silenciosa y destructiva**, que es exactamente lo que el SRS prohíbe.

En el hallazgo original del Sprint 2 (documentado en `docs/checklist_m2.md` §5) la mitigación fue solo conductual: "el orquestador solo escribe la vela vigente". Eso reduce la probabilidad del evento, pero **no satisface el requisito**: cualquier reintento, duplicación de ciclo, reejecución manual o nuevo módulo reescribiría el histórico sin oposición.

### Qué exige el SRS vs. qué hace el motor

| | Contenido |
|---|---|
| **SRS §4.4 exige** | Que un registro de precio histórico **no pueda** ser modificado ni destruido, sin importar qué proceso intente escribir. |
| **InfluxDB 2.7 hace** | Sobrescribe el punto en silencio si coinciden measurement + tags + timestamp. **No existe** modo *insert-if-absent* nativo a nivel de punto. |
| **Brecha** | El requisito no se puede delegar al motor: debe imponerlo la capa de aplicación. |

### Cómo se resolvió

Guarda *insert-if-absent* en la capa de aplicación (`src/ingestion/engine.py`):

- **`write_to_influx()`** (firma aprobada del SDD §5.1.2, **sin cambios en sus parámetros**): antes de escribir, consulta InfluxDB por el timestamp exacto. Si ya existe → **no escribe** y emite `WARNING` ("Escritura omitida: la vela con timestamp … ya existe en el bucket …"). Si no existe → escribe normalmente.
- **`existing_timestamps()` + `write_candles_batch()`**: rutas nuevas para el backfill del Sprint 3. Una **única consulta de rango** para toda la secuencia y una **única escritura por lote** con las velas faltantes — no una consulta por vela. Devuelven `(escritas, omitidas)`.
- La verificación corre dentro del mismo `asyncio.to_thread` de la escritura, de modo que no se abre un hilo adicional por vela.

### Limitación declarada (no oculta)

La guarda es **read-then-write**: existe una ventana teórica de condición de carrera entre la consulta y la escritura. Con el orquestador actual de **un solo escritor** y una vela cada 5 minutos es un riesgo despreciable, pero **no es una garantía transaccional del motor**. Para eliminarla por completo haría falta o bien un almacén con constraint único real (PostgreSQL ya lo es, y `transactions`/`ai_predictions` usan UUID), o bien un mecanismo de bloqueo del bucket. Se deja explícito para que nadie interprete esto como inmutabilidad fuerte de InfluxDB.

### Verificación

| Prueba | Resultado |
|---|---|
| Suite unitaria (`tests/test_engine.py`, 10 tests nuevos) | Timestamp nuevo → escribe; timestamp existente → **no escribe + WARNING**; vecino distinto → sí escribe; lote parcial → escribe solo lo faltante; lote 100% existente → cero escrituras; **una sola consulta por lote** |
| **Contra InfluxDB real (06/10/2026)** | Intento de reescribir la vela real `1791316500000` con `price_open=999, price_close=888, volume=777` → `WARNING Escritura omitida…` y lectura de vuelta: **`price_open=759.51001`, `price_close=759.60999`, `volume=1736118.0`** — valores originales intactos |

### Cambio propuesto

- **SRS §4.4:** dejar constancia de que la inmutabilidad del histórico en InfluxDB es una **propiedad impuesta por la capa de aplicación** (guarda insert-if-absent), no una garantía del motor de series temporales. El requisito en sí no se relaja: se cumple igual.
- **SDD §5.1:** documentar la guarda en la especificación de `write_to_influx()`. La firma pública **no cambia**; se añaden `existing_timestamps()` y `write_candles_batch()` como operaciones de soporte del backfill.

### Impacto

Bajo. Es una operación de lectura previa adicional en el mismo hilo de escritura (medida: sub-milisegundo sobre InfluxDB local). No rompe ninguna firma aprobada ni modifica datos ya ingeridos. La suite completa pasó de **53 a 63 tests** en verde.

### Interacción con CR-001 (migración de datos, 17/10/2026)

La guarda *insert-if-absent* **también bloquea la adición de campos a velas ya existentes**: no reescribe un punto existente, así que `price_high`/`price_low` (CR-001) no podrían añadirse al histórico ya ingerido. Se resuelve con la **migración única** `src/ingestion/rebuild.py --confirm-wipe`, que borra los puntos del bucket y lo reingiere completo con la firma de 5 campos desde la propia fuente.

Queda explícito por escrito que esta operación:

- **no forma parte del pipeline**: no la llama el orquestador, el backfill ni ningún proceso automático;
- **exige confirmación explícita** (`--confirm-wipe`) y se niega sin ese flag;
- **es previa a producción** y se ejecuta una sola vez, antes de ampliar el backfill, para no reconstruir dos veces;
- **no viola el SRS §4.4**: la inmutabilidad protege el histórico *durante la operación normal*; esta acción es una migración consciente de un administrador sobre datos que se vuelven a obtener reproduciblemente desde el proveedor.

Ver el detalle completo en CR-001, sección "Migración de datos".

---

## CR-004 — Superficie de la API de M2 en Fase B: solo lectura, sin sesión, y estado `CERRADO`

| Campo | Valor |
|---|---|
| **Fecha de detección** | Sprint 3, Fase B1 — 20/10/2026 |
| **Solicitante** | Equipo de desarrollo (Naranja's Team) |
| **Documentos afectados** | SRS v1.0 §6.2 (RNF-001, RNF-003, RNF-005); SRS §10.3 (estados visuales); SDD v1.0 (no prevé la interfaz HTTP de M2) |
| **Tipo** | Alcance aceptado / adición de interfaz |
| **Prioridad** | Media (la brecha de seguridad se cierra en el Sprint 4) |
| **Estado** | Implementado y verificado en vivo; pendiente de aprobación formal |

### Contexto

El backlog Ítem 8 (Dashboard) exige que la interfaz «se comunica con la API de la Máquina 2», pero ni el SRS ni el SDD v1.0 describen esa API: ni sus rutas, ni su alcance de escritura, ni su modelo de sesión. La Fase B1 la construye (`src/endpoints/`) y al hacerlo toca tres puntos del SRS que conviene dejar escritos:

1. **RNF-001** supone un «Iniciar Sesión» entre las acciones críticas de 2 toques → en algún momento debe existir sesión.
2. **RNF-005** exige que ningún dato salga del hardware local y que no haya accesos no autorizados.
3. **SRS §10.3** define tres estados visuales (verde, amarillo, rojo parpadeante del Kill-Switch) → el sistema apagado no tiene estado propio.

### Cambio propuesto (implementado en Fase B1)

La API de M2 queda definida como **SOLO GET**, con siete rutas y sin ninguna operación de escritura:

| Ruta | Contenido |
|---|---|
| `GET /api/health` | vida del proceso |
| `GET /api/status` | regla de estado (verde/amarillo/cerrado) + IA + configuración + veredicto de señal |
| `GET /api/candles` | velas 5m, con agregación 15m/1h/1d resuelta en Flux |
| `GET /api/predictions` (+ `/latest`) | historial con resultado real (una sola consulta a InfluxDB por página) + última predicción con su señal |
| `GET /api/yield` | rendimiento virtual 2x, siempre con `virtual: true` |
| `GET /api/model-quality` | nota permanente de calidad del modelo (D5), también con M1 caído |

más `GET /modelo` en el servicio de M1 (contrato del artefacto: ventana, banda, acierto direccional **con su fuente**).

Y dos comportamientos que el SRS no nombra:

- **Estado `CERRADO`**: neutro, fuera de la sesión NYSE. Sin él, la antigüedad del dato dejaría el sistema en amarillo 17 horas cada día por la sola ausencia de ticks que no existen (SRS §10.3 solo prevé verde/amarillo/rojo; el rojo es exclusivo del Kill-Switch y no se usa en esta fase).
- **Errores en español** (`{"mensaje": ...}` en 404/405/422/503), sin stack traces ni claves crudas.

### Limitación declarada (no oculta)

La API **no tiene autenticación, ni usuarios, ni roles**. Es una brecha **declarada** frente a RNF-001 (login) y RNF-005 (autorización), no una omisión accidental:

- Mitigación en esta fase: `127.0.0.1` por defecto (no se publica a la intranet), SOLO GET (un POST contesta 405, no ejecuta), sin credenciales ni secretos en ninguna respuesta.
- Cierre: **Sprint 4**, junto con `trader_users`, permisos por rol, el Kill-Switch y la deuda D4 del backlog (rol de solo lectura en PostgreSQL).
- El contrato está fijado por tests (`tests/test_endpoints_*.py`) para que ampliarlo después no rompa nada en silencio.

### Verificación

- 634 pruebas en verde (`pytest -q`); 65 cubren ciclo de predicción y endpoints, y 7 el `GET /modelo`.
- En vivo sobre la máquina de desarrollo, en Docker (20/10/2026): `api` healthy; `/api/status` devolviendo `CERRADO` con IA disponible; **ciclo completo scheduler → M1 → `ai_predictions`** con una predicción real persistida (UP, confianza 0,28 → correctamente no operable frente al umbral 0,75); `/api/candles`, `/api/yield` y `/api/model-quality` sirviendo datos reales; y la rama de fallo vista también en vivo: con M1 inalcanzable el ciclo registró el error y **no persistió nada**.

### Impacto

Funcional: ninguno sobre lo ya aprobado — no modifica ningún RF-XXX ni RNF-XXX existente; añade la interfaz que el Ítem 8 necesita. La medición de RNF-003 (ver `docs/backlog.md`) queda **provisional** hasta que la quinta componente, la actualización de la interfaz, exista en la Fase B2.

---

## CR-005 — Kill-Switch del Sprint 4: estado persistido, inmutabilidad de la auditoría y broker simulado

| Campo | Valor |
|---|---|
| **Fecha de detección** | Sprint 4 — alcance mínimo aprobado (Ítems 9, 10 y parte del 12) |
| **Solicitante** | Equipo de desarrollo (Naranja's Team) |
| **Documentos afectados** | SDD §3.1.1 (esquema físico), §5.4 (`RiskManager`), §8 (trazabilidad RF-402/501/502/601); SRS §3.5 (Feature 5), §3.7.1 (RF-701), RNF-010; SRS §4.2.5 (`AuditLog`) |
| **Tipo** | Adición de diseño / interpretación de requisito |
| **Prioridad** | Alta (es la pieza de protección de capital) |
| **Estado** | Implementado y verificado en PostgreSQL real; pendiente de aprobación formal |

### Contexto

El SRS describe **qué** debe hacer el Kill-Switch (RF-501/502/503) y el SDD
le da una clase (`KillSwitchEngine`, `RiskManager`, `BrokerGateway`), pero
cuatro cosas no están decididas en ningún documento aprobado y hubo que
decidirlas para poder implementar:

1. **Dónde vive el estado.** RF-501 exige que el bloqueo sobreviva a un
   reinicio del proceso; el SDD no prevé ninguna tabla para eso.
2. **Qué significa «prioridad de hilos de CPU»** (RNF-010) en Python, que no
   tiene prioridades de hilo utilizables sin privilegios de root.
3. **Con qué broker** se liquida: el Ítem 12 (TCP real con IB) quedó fuera
   del alcance mínimo, pero RF-601/602 exigen el Modo Sombra.
4. **Qué garantiza que la auditoría es «inalterable»** (RF-701, SRS §3.7.1):
   el SRS solo lo declara para InfluxDB (§4.4), no para PostgreSQL.

### Cambio aprobado (implementado en C1–C4)

**1. Esquema (SDD §3.1.1):**

- Tabla nueva **`kill_switch_state`** (singleton por convención, como
  `system_configurations`): `activo`, `motivo`, `origen`
  (`MANUAL`|`AUTOMATICO`), `actor` (`SISTEMA`|`OPERADOR`), recorrido de la
  liquidación (`NINGUNA`→`PENDIENTE`→`EN_CURSO`→`COMPLETADA`/`FALLIDA`),
  acumulado de liquidaciones, `reinicio_en` + `sesion_reinicio` (guarda
  anti-bucle del disparo automático) y el contador de intentos de reinicio.
- Columna nueva **`actor`** en `audit_logs` (`VARCHAR(50) NOT NULL`, CHECK
  `SISTEMA|OPERADOR|CLAVE_REINICIO`, **sin default permanente**). Migración
  idempotente en un bloque `DO` colocada **antes** de los triggers, porque su
  backfill es un `UPDATE`: con el trigger puesto, la propia migración se
  rechazaría. En el volumen previo se rellena con `SISTEMA`.

**2. Inmutabilidad de `audit_logs` a nivel de BASE DE DATOS (RF-701):**

- `BEFORE UPDATE OR DELETE … FOR EACH ROW` → `RAISE EXCEPTION`.
- `BEFORE TRUNCATE … FOR EACH STATEMENT` → `RAISE EXCEPTION` (los triggers
  por fila **no** se disparan ante un `TRUNCATE`: sin este, la tabla
  entera se podría vaciar en una sentencia).
- `ENABLE ALWAYS` en ambos, y `REVOKE UPDATE, DELETE, TRUNCATE … FROM
  PUBLIC` como segunda muralla.
- **Riesgo residual declarado:** el servicio entra como superusuario, que
  salta los permisos (los triggers, no). Dejar de entrar como superusuario
  —rol de mínimo privilegio— queda como trabajo futuro.

**3. Interpretación de RNF-010 («prioridad a nivel de hilos de CPU»):**

No aplica literalmente en Python. Lo implementado, y lo que se reporta como
cumplimiento, es: (a) el camino de activación no tiene ni un `await` ni una
llamada a la base **antes** de poner la bandera en memoria; (b) todo lo que
emite órdenes consulta la bandera antes que cualquier cálculo;
(c) las tareas de orden pendientes se cancelan al activarse; (d) se miden
pulsación→bloqueo y pulsación→liquidación (ver §6 del informe).

**4. Orden de ejecución y regla 3 (SDD §7):**

`(a)` bandera en memoria → `(b)` compare-and-set en PostgreSQL (mejor
esfuerzo) → `(c)` liquidación → `(d)` auditoría (mejor esfuerzo, con
respaldo en `logs/auditoria-pendiente.jsonl` y volcado posterior). **La
regla 3 estricta no aplica a este camino**: un botón de pánico que esperara
a PostgreSQL no sería un botón de pánico. Sí aplica —sin excepciones— al
resto de acciones (órdenes, cambio de modo, reinicio). Si el estado no se
puede leer, se **falla cerrado**: no se emiten órdenes.

**5. Broker simulado en lugar de IB real (RF-402, RF-601, RF-602):**

`BrokerGateway` (Protocol) + `SimulatedBrokerGateway`, que registra todo con
`is_simulated = true` y lo liga a su `prediction_id`. El precio sale de un
callable inyectado **antes** de escribir: si la fuente no responde, no queda
una orden con precio inventado. Cambiar a TCP real es implementar el mismo
Protocol en `src/integration/`, sin tocar `src/security/`.

**6. Desviación sobre el SDD §5.4:** `evaluate_order_risk` delega en
`perfiles.umbral_de` (que **lanza** ante un perfil desconocido) en lugar del
`.get(perfil, 0.75)` del SDD, que caería en silencio al umbral por defecto.

**7. Disparo automático:** en cada tick del scheduler, tras persistir la
vela, contra el precio de **apertura de la sesión** con
`AsymmetricCalculator.is_threshold_breached`. Actor `SISTEMA`, origen
`AUTOMATICO`. Dispara **aunque no haya posiciones** (lo que importa es
bloquear, RF-503). Tras un reinicio, no se vuelve a evaluar hasta la
siguiente sesión. Si es un gap (RF-203), el payload registra la pérdida
**real** y su diferencia con el límite.

### Verificación (contra el motor real, no supuestos)

- **PostgreSQL real** (`ritech-postgres16`): `UPDATE`, `DELETE` y
  `TRUNCATE` rechazados con un INSERT de control en la misma transacción
  (psql sale con `ON_ERROR_STOP` y la transacción se revuelve sola: no
  queda ningún marcador); `tgenabled = 'A'` leído del catálogo; el `REVOKE`
  probado con un rol **no superusuario**; idempotencia del DDL (dos
  aplicaciones seguidas).
- **Idempotencia del disparo:** el compare-and-set probado en vivo —una
  sola ganancia por activación—, más la prueba de «pulsar dos veces no
  liquidó dos veces» (el acumulado `liquidaciones` lo demuestra en la base).
- **878 pruebas** en verde (`pytest -q`), 47 de frontend (`node --test`),
  `oxlint` y `vite build` sin avisos.
- **Latencias RNF-010 con su desglose** (informe §4.1–§4.2): son **cuatro
  cifras de cuatro pasos distintos y ninguna incluye a otra** —
  (1) bandera en memoria p50 0,003 / p95 **0,004 ms** (sin I/O);
  (2) **compare-and-set contra PostgreSQL real** p50 **7,81** / p95
  **11,83 ms** (`UPDATE … WHERE activo = FALSE` + `commit`);
  (3) liquidación p50 0,027 / p95 **0,038 ms** contra
  **`SimulatedBrokerGateway` con repositorio en memoria** — **es
  simulada: contra Interactive Brokers real queda sin medir** (Ítem 12);
  (4) pulsación→respuesta HTTP p95 **1,66 ms** con dobles de base y p95
  **40,06 ms** con PostgreSQL real. El `INSERT` de `audit_logs` no está en
  ninguna de ellas (medirlo exigiría escribir en una tabla inmutable).
  `ResultadoDisparo` expone las tres primeras por separado
  (`ms_bloqueo`, `ms_persistencia`, `ms_liquidacion`) y también van en la
  respuesta de `POST /api/kill-switch` y en el `payload` del evento.

### Impacto

Funcional: ninguno sobre lo ya aprobado — no modifica ningún RF/RNF
existente; añade el estado y la garantía que RF-501 y RF-701 exigen. Deuda
abierta: el riesgo del superusuario (arriba) y el Ítem 12 (IB real).

---

## CR-006 — Autenticación mínima del Sprint 4: sin usuarios ni roles, con clave de reinicio

| Campo | Valor |
|---|---|
| **Fecha de detección** | Sprint 4 — recorte de alcance por falta de tiempo |
| **Solicitante** | Equipo de desarrollo (Naranja's Team) |
| **Documentos afectados** | SRS §3.4.2 (OTP para el Modo Autónomo), RF-402, RF-503, RNF-001 (login en 2 toques), RNF-005 (aislamiento); SDD §3.1.1 (`trader_users`, `totp_secret`), §4.3 (transición de modos con OTP); CR-004 (superficie de la API) |
| **Tipo** | Alcance aceptado / brecha declarada |
| **Prioridad** | Alta (seguridad), pero **postergada** por el recorte |
| **Estado** | Implementado lo mínimo; **brechas declaradas, no cerradas** |

### Contexto

El plan original del Sprint 4 incluía **S4.2: login mínimo y roles**
(`trader_users` con bcrypt, tres roles, sesiones en BD con cookie
`httpOnly` + `SameSite=Strict`, protección CSRF, script de creación de
usuarios y límite de intentos de login). El alcance se recortó por falta de
tiempo y S4.2 **se eliminó**.

Lo que sí se conservó es la única decisión de autorización que el
Kill-Switch necesita: **disparar no pide nada, reiniciar sí**.

### Cambio aprobado (implementado en C3)

**Clave de reinicio del Kill-Switch**, en lugar de usuarios y sesiones:

- **Hash en el entorno**, nunca la clave en claro y nunca en el repositorio:
  `KILL_SWITCH_RESET_KEY_HASH` con formato
  `pbkdf2-sha256$<iteraciones>$<sal>$<hash>`, todo generado con la
  **stdlib** (`python -m src.security.clave_reinicio`, que lee la clave dos
  veces sin eco y nunca por argumento de línea de comandos).
- **Comparación en tiempo constante** con `hmac.compare_digest`. Un hash mal
  formado **cierra** el acceso (devuelve `False` y loguea), nunca lo abre.
- **Límite de intentos** (`KILL_SWITCH_MAX_ATTEMPTS`, 5 por defecto) dentro
  de una ventana (`KILL_SWITCH_VENTANA_INTENTOS_S`, 900 s), **persistido en
  `kill_switch_state`**: sobrevive a un reinicio del proceso y se limpia al
  reiniciar con éxito o al caducar la ventana.
- **Actor `CLAVE_REINICIO`** en `audit_logs`: sin identidad personal, porque
  no existe autenticación de usuarios. La IP del equipo acompaña siempre.
- **Disparar sigue sin clave** (RF-501): en una emergencia no se pide
  credencial a quien intenta parar el sistema.

**Sin CSRF:** la protección CSRF protege credenciales ambientales (cookies).
Al no haber sesión ni cookie, no hay nada ambiental que explotar: la clave
viaja en la cabecera `X-Kill-Switch-Key`, que es un secreto de la petición,
no una credencial automática del navegador.

**Sin TOTP ni roles:** el cambio a Modo Autónomo sigue exigiendo en el SRS
la autorización de la Gerencia + OTP (§3.4.2, RF-402); en esta fase **no
existe endpoint de cambio de modo**: `is_autonomous` solo se toca en la
configuración, y el flujo completo se prueba con predicciones y modo
inyectados en los tests de integración (`tests/test_flujo_ordenes.py`),
etiquetados como verificación de pipeline y no de rendimiento del modelo.

### Brechas declaradas (no cerradas)

| Requisito | Estado | Mitigación actual |
|---|---|---|
| **RF-503** — reinicio por «Administrador de TI o Gerente» | **Incumplido en identidad**: cualquiera con la clave reinicia; no hay forma de saber *quién*. | Actor `CLAVE_REINICIO` + IP en la auditoría, límite de intentos, clave de fuente alta entropía. |
| **RNF-001** — «Iniciar Sesión» en 2 toques | **No implementado** (S4.2 eliminado). | Ninguna: la UI no tiene sesión. |
| **RNF-005** — aislamiento / accesos no autorizados | **Parcial**: no hay autorización por rol; las rutas GET siguen abiertas (CR-004 se mantiene en lo esencial). | API en `127.0.0.1` por defecto, sin secretos en respuestas, solo-loopback en Docker. |
| **SRS §3.4.2 / RF-402** — OTP para Modo Autónomo | **No implementado**. | El modo no se puede cambiar por API. |
| **SRS §4.3 (SDD)** — transición de modos con OTP | **No implementado**. | Ídem. |
| TLS en la intranet | **No implementado**. | Red física de la oficina (REST-05); queda como trabajo futuro. |

**Nota sobre RNF-010:** las cifras de latencia y **qué mide exactamente
cada una** están desglosadas en **CR-005** y en `docs/informe-sprint4.md`
§4.1: banda en memoria, compare-and-set contra PostgreSQL real, liquidación
y pulsación→respuesta HTTP son cuatro medidas distintas que no se suman.
La liquidación medida es contra `SimulatedBrokerGateway` (**simulada**); el
tiempo **contra Interactive Brokers real queda sin medir** hasta que exista
el Ítem 12.

### Dependencias

**Ninguna nueva.** El recorte de S4.2 dejó `bcrypt` sin uso: la clave de
reinicio usa PBKDF2 de la biblioteca estándar, y no se añadió ningún
paquete a `pyproject.toml` en todo el Sprint 4.

### Cierre previsto

Junto con el Ítem 12 (IB real) o en el siguiente sprint, según decida la
Gerencia: `trader_users` + sesiones + TOTP, rol de mínimo privilegio en
PostgreSQL (deuda D4 de CR-004) y TLS. Hasta entonces, cualquier revisión
de seguridad debe leer **esta** CR antes que el código.
