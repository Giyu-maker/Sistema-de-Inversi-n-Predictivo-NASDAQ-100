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
