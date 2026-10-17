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
| **Estado** | Propuesto — pendiente de aprobación formal |

### Hallazgo
- SRS §3.1.2 (narrativa de la Feature 1) establece que "la API devuelve el precio de apertura, cierre, **máximo, mínimo** y volumen de la vela de 5 minutos".
- SRS §4.2.2 (Diccionario de Datos, entidad `NasdaqTick`) y SDD §5.1 (firma de `write_to_influx`) solo contemplan `price_open`, `price_close` y `volume` — **sin** `price_high` / `price_low`.
- Es una inconsistencia real entre la narrativa del documento y sus propios artefactos formales (diccionario de datos y firma de clase), no una omisión introducida por el equipo de desarrollo.

### Cambio propuesto
- Agregar `price_high: Float` y `price_low: Float` a la entidad `NasdaqTick` en SRS §4.2.2.
- Actualizar la firma de `write_to_influx()` en SDD §5.1 para incluir ambos parámetros.

### Justificación
Estos valores son necesarios como features de entrada para el modelo predictivo de IA (Feature 3, RF-301 a RF-304) planificado para Sprint 3. Omitirlos ahora obligaría a rediseñar el esquema de InfluxDB más adelante, con el costo de reprocesar o perder histórico ya capturado.

### Impacto
Bajo. Cambio aditivo sobre dos documentos aprobados; no rompe compatibilidad con datos ya ingeridos.

### Estado de implementación
El modelo Pydantic `NasdaqCandle` en el código ya valida y captura ambos campos. La firma pública de `write_to_influx()` se mantuvo **literal al SDD v1.0** mientras esta CR no se apruebe formalmente, para no romper trazabilidad con el documento aprobado. Marcado como `TODO(SRS-4.2.2)` en el código.

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
