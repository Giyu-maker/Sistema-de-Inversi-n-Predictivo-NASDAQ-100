# Matriz de cumplimiento — SRS v1.1 (RiTech-NASDAQ100)

**Fuente de verdad:** `docs/srs.md` (v1.1 aprobado). Una fila por cada
`RF-XXX`, `RNF-XXX` y `REST-XXX` del documento: **22 RF + 11 RNF + 6 REST =
39 requisitos**.
**Fecha:** 05/11/2026 · **Sprints 1–4 + FASE D** (SS-9…SS-13; revisada el 05/11/2026).

### Cómo se lee el estado

| Estado | Significado |
|---|---|
| **Cumple** | Implementado y verificado con el sistema real donde el requisito exige realidad (base de datos, cálculo con datos de mercado, contrato de la API, navegador). |
| **Parcial** | Implementado, pero la verificación fue **solo con datos inyectados o simulados**, o falta una pieza material (interfaz, identidad, hardware, observación en vivo). |
| **No cumple** | Debe existir y no existe. |
| **Futuro** | Fuera del alcance ejecutado hasta la fecha; documentado como trabajo pendiente. |

> **Regla aplicada:** todo lo que solo se probó en banco de pruebas —datos
> inyectados, broker simulado, mediciones sin observación en vivo— se marca
> **Parcial**, aunque el código esté completo. Un «Cumple» de esta matriz
> tiene que poder señalarse con algo que haya corrido de verdad.

### Resumen

| Estado | Cantidad |
|---|---|
| Cumple | 15 |
| Parcial | 22 |
| No cumple | 0 |
| Futuro | 2 |
| **Total** | **39** |

---

## 1. Requisitos funcionales (RF)

| Req. | Estado | Evidencia | Brecha | CR |
|---|---|---|---|---|
| **RF-101** Frecuencia de 5 min | **Cumple** | `src/ingestion/scheduler.py` (`DEFAULT_INTERVAL_S=300`), `tests/test_scheduler.py::test_run_respeta_la_cadencia…`, ticks reales en M2 (informe B1) | — | — |
| **RF-102** Monitoreo extendido (pre/regular/after) | **Parcial** | CR-002, `src/ingestion/providers/twelve_data.py`, informe B1 | Plan *Basic* de Twelve Data: solo horario regular (78 de ~192 velas); pre-market y after-hours sin ingerir | CR-002 |
| **RF-103** Series en InfluxDB local (epoch ms) | **Cumple** | `src/ingestion/engine.py::write_to_influx` + `existing_timestamps` (guarda insert-if-absent), verificado contra InfluxDB real | — | CR-003 |
| **RF-201** Ganancias apalancadas 2x | **Cumple** | `src/calculator/asymmetric.py::calculate_yield`, `tests/test_asymmetric.py`, `/api/yield` con datos reales | — | — |
| **RF-202** Límite de pérdida 1,25 % | **Parcial** | `AsymmetricCalculator.MAX_LOSS_PCT` y su evaluación en vivo en `/api/yield`; disparo automático en `scheduler._evaluar_umbral_de_perdida` | La evaluación corre contra datos reales cada tick, pero **el disparo resultante solo se observó con series inyectadas**: no hay un cruce real de −1,25 % registrado | CR-005 |
| **RF-203** Gaps de mercado | **Parcial** | `is_market_gap()` / `gap_loss_notice()`; payload del disparo con pérdida real (`test_scheduler::test_un_gap_registra_la_perdida_real…`) | El aviso de gap **con pérdida real** se probó con datos inyectados; sin gap real observado en un disparo | CR-005 |
| **RF-204** Exclusión por volatilidad extrema | **Futuro** | `src/calculator/asymmetric.py` (`TODO(RF-204)`), `docs/backlog.md` (deuda desde el Sprint 2) | No implementado: no existen las ventanas de exclusión de los primeros/últimos 15 min | — |
| **RF-301** Entrenamiento local estricto | **Parcial** | `src/ai/train.py`, `docs/informe-entrenamiento-sprint3.md` | Entrenamiento verificado en **CPU** (M2); **no se ha ejecutado en la GPU de M1** (CUDA), que es lo que el requisito pide | — |
| **RF-302** Reentrenamiento diario automatizado | **Parcial** | `src/ai/train.py` (CLI), `deploy/m1/` (unidad + `promote.sh`), `docs/despliegue.md` §8 | Construido y documentado, pero **no se ha observado un ciclo diario completo** en M1 | — |
| **RF-303** Explicabilidad (XAI) | **Cumple** | `src/ai/xai.py`, columna `ai_predictions.technical_rationale`, `docs/informe-entrenamiento-sprint3.md` | — | — |
| **RF-304** Ratio de confianza y umbral 75 % | **Cumple** | `src/security/perfiles.py::evaluar_senal` (decisión D7), `tests/test_perfiles.py`, predicciones reales del Sprint 3 clasificadas por debajo del umbral | — | — |
| **RF-401** Modo Asesor (recomienda, no opera) | **Cumple** | `RiskManager.decidir` → `ALERTA` sin orden, `tests/test_flujo_ordenes.py::TestModoAsesor`, panel operando en Asesor | — | — |
| **RF-402** Modo Autónomo (órdenes sin intervención) | **Parcial** | `tests/test_flujo_ordenes.py::TestModoAutonomo`: orden simulada ligada a su `prediction_id` | Solo con predicciones **inyectadas** y broker simulado; **sin autorización gerencial, sin OTP y sin IB real** | CR-006, Ítem 12 |
| **RF-403** Tres perfiles de riesgo | **Parcial** | `src/security/perfiles.py` (0,85 / 0,75 / 0,65), `system_configurations`, `tests/test_flujo_ordenes.py::los_tres_perfiles_cambian_el_umbral` | Los perfiles se aplican, pero **no hay interfaz ni endpoint para configurarlos**: se cambian escribiendo en la base | CR-004, CR-006 |
| **RF-501** Botón único de emergencia (2 toques) | **Parcial** | `frontend/src/components/control-kill-switch.tsx` (pulsar + confirmar), `tests/killSwitch.test.mjs`, `POST /api/kill-switch` | **Sin observación end-to-end en navegador** contra el API real: el escenario `kill-switch-activo` existe justamente para esa captura | CR-005 |
| **RF-502** Liquidación síncrona de posiciones | **Parcial** | `KillSwitchEngine.execute_global_liquidation` + `SimulatedBrokerGateway.liquidar_todo`, informe §4 | La liquidación es **simulada**: contra Interactive Brokers real no existe ni se ha medido | CR-005, Ítem 12 |
| **RF-503** Bloqueo de reapertura | **Parcial** | `RiskManager.decidir` → `BLOQUEADO`, `tests/test_endpoints_kill_switch.py`, disparo sin clave | El bloqueo está verificado, pero el reinicio **no exige identidad** (ADMIN_TI o GERENTE): solo la clave de reinicio | CR-006 |
| **RF-601** Modo Sombra con datos reales | **Parcial** | `SimulatedBrokerGateway` cableado a `LectorInflux` (precios reales de InfluxDB) | **Ninguna liquidación ejecutada con precios de mercado reales**: las pruebas usan series inyectadas y un precio de prueba | CR-005 |
| **RF-602** Trazabilidad `is_simulated = true` | **Cumple** | `transactions.is_simulated DEFAULT TRUE` (esquema), `SimulatedBrokerGateway` (siempre `True`), `tests/test_flujo_ordenes.py` y `tests/test_broker.py` | Aún sin filas reales, porque no se ha emitido ninguna orden en producción | — |
| **RF-701** Conservación 5 años e inmutabilidad | **Parcial** | Triggers de `audit_logs` (fila + TRUNCATE, `ENABLE ALWAYS`) **verificados en PostgreSQL real** — informe §3; **demo manual de los tres ataques** en `scripts/demo_inmutabilidad.sql` (2 INSERT aceptados y 3 ERROR del trigger, cada ataque en su transacción con ROLLBACK); visor de solo lectura `GET /api/auditoria` + `tests/test_endpoints_auditoria.py` (un POST contesta 405) | La inmutabilidad está verificada y **ya es visible**; los **5 años** siguen siendo una política de respaldos **sin herramienta de purga** (SRS §4.4) ni medición de retención | CR-005 |
| **RF-702** Reportes PDF/CSV | **Parcial** | `GET /api/reportes/{tipo}` con `senal`, `rendimiento`, `kill-switch` y `auditoria` en CSV (UTF-8 con BOM) y PDF (fpdf2); `src/utils/reportes.py`, sección del panel, `tests/test_reportes.py` (26) y `frontend/tests/reportes.test.mjs` (7); verificado en vivo para los tipos que leen PostgreSQL | El SRS pide **rendimiento asimétrico acumulado**, **tasa de efectividad agregada** e **histórico de posiciones cerradas**: los dos primeros salen por fila (por sesión y por predicción) y el tercero **no tiene tipo de reporte** (falta `transactions`). Además `senal`/`rendimiento` solo se probaron con dobles: sin InfluxDB responden 503 | CR-007 |
| **RF-801** Runbook de fallos de IB | **Cumple** | `docs/runbook-broker.md` (entregado a operadora y secretaria), enlazado desde `docs/despliegue.md` | Los pasos para fallo de IB real quedan escritos pero **no probados** (Ítem 12) | CR-005 |

## 2. Requisitos no funcionales (RNF)

| Req. | Estado | Evidencia | Brecha | CR |
|---|---|---|---|---|
| **RNF-001** Acciones críticas en ≤ 2 toques | **Parcial** | Kill-Switch a dos toques (`tests/killSwitch.test.mjs`, `control-kill-switch.tsx`) | De los tres casos del SRS, solo existe el Kill-Switch: **no hay «Iniciar Sesión» ni «Activar Modo Autónomo»** | CR-006 |
| **RNF-002** Validación con Zod + React Hook Form | **Parcial** | Zod en `frontend/src/api/esquemas.ts` + `cliente.ts`; errores en español (`src/endpoints/errores.py`) | **React Hook Form no está instalado**: el único formulario real (clave de reinicio) valida a mano | CR-006 |
| **RNF-003** < 500 ms en el servidor local | **Parcial** | `scripts/bench_rnf003.py` — p95 **117 ms**, 4 de 5 componentes; `scripts/bench_rnf003_ui.mjs` — render 39 ms (informe B2) | **Sin cumplimiento completo**: falta la observación end-to-end con vela real, y la inferencia corrió en CPU | — |
| **RNF-004** Carga inicial < 1,5 s | **Cumple** | `docs/informe-b2-sprint3.md` §2: mediana **28 ms**, FCP 68 ms, `/api/status` 114 ms | — | — |
| **RNF-005** Aislamiento de datos | **Parcial** | Docker solo en loopback (`127.0.0.1`), cero servicios cloud (REST-01), sin secretos en respuestas | **Sin autorización por rol** y con las rutas GET abiertas; sin TLS | CR-004, CR-006 |
| **RNF-006** Encriptación AES-256 en reposo | **Cumple** | `src/security/crypto.py` (AES-256-GCM), claves `*_ENC` en `.env`, descifrado en cada arranque del scheduler | Sin test automatizado dedicado del módulo (la verificación del Sprint 2 está en el checklist) | — |
| **RNF-007** SLA 99,9 % | **Parcial** | Healthchecks de Docker y `GET /api/health` | **No hay medición acumulada** de disponibilidad ni registro de uptime | — |
| **RNF-008** Mantenimientos después de las 18:00 CO | **Cumple** | `docs/despliegue.md` §8: reprogramación diaria a las **20:30 CO** (después de las 18:00) | Es una política operativa: ningún control impide programar mantenimiento en horario de sesión | — |
| **RNF-009** Tolerancia a caídas de red | **Parcial** | `test_run_sobrevive_a_un_fallo_de_red`, puerta de sesión (P1.9), deduplicación por insert-if-absent; **alerta al operador** con `src/integration/notificaciones.py` (correo SMTP real y bandeja SMS simulada, `NOTIFICACION_ENVIADA`/`NOTIFICACION_FALLIDA` en `audit_logs`) | Sin IB real: **la suspensión de órdenes ante caída de red no se puede observar**; el aviso de brecha cubre la pérdida de datos, no la del broker | Ítem 12, CR-007 |
| **RNF-010** Prioridad del botón de pánico | **Parcial** | Informe §4 con la **interpretación** y las cuatro cifras rotuladas; `ResultadoDisparo`; CR-005 | En Python no hay prioridades de hilo reales (interpretación escrita) y la liquidación es **simulada**: el tiempo contra IB real **queda sin medir** | CR-005 |
| **RNF-011** Tipado estricto | **Cumple** | `frontend/tsconfig.app.json` (`strict: true`) y **cero `any`** en `frontend/src`; Pydantic con `extra="forbid"` en todos los esquemas de salida | — | — |

## 3. Restricciones (REST)

| Req. | Estado | Evidencia | Brecha | CR |
|---|---|---|---|---|
| **REST-01** Cero nube | **Cumple** | `docker-compose.yml` (todo en M2), ningún servicio cloud en el código | El único servicio externo es Twelve Data (datos de mercado), permitido por CR-002 | CR-002 |
| **REST-02** Confidencialidad de la IA | **Cumple** | Entrenamiento e inferencia en `src/ai/`, locales; `docs/informe-entrenamiento-sprint3.md` | — | — |
| **REST-03** Presupuesto ≤ $20.000.000 COP | **Parcial** | Sin registro de gasto en el repositorio | **Lo confirma la Gerencia**: aquí no hay evidencia verificable | — |
| **REST-04** Producción a principios de noviembre de 2026 | **Parcial** | Sprint 4 cerrado el 03/11/2026 (`docs/informe-sprint4.md`) | Falta el **despliegue en producción (M1/M3)** y su verificación física | — |
| **REST-05** Residencia de datos en Colombia | **Cumple** | Bases publicadas solo en loopback (`docker-compose.yml`), sin cloud, datos en M2 | — | — |
| **REST-06** Pentesting (noviembre 2026) | **Futuro** | — | No ejecutado: fase final de despliegue, responsabilidad de Naranja's Team | — |

---

## 4. Índice de Change Requests

| CR | Tema | Estado |
|---|---|---|
| **CR-001** | Campos `price_high` / `price_low` en `NasdaqTick` | **Aprobada e implementada** — SRS/SDD v1.1; migración `rebuild --confirm-wipe` ejecutada el 17/10/2026 |
| **CR-002** | Proveedor Alpha Vantage → Twelve Data | **Aprobada e implementada** — deja RF-102 **parcial** (solo horario regular) |
| **CR-003** | Inmutabilidad del histórico en InfluxDB | **Resuelta en la capa de aplicación** (guarda insert-if-absent), verificada contra InfluxDB real |
| **CR-004** | Superficie de la API de M2: SOLO GET, sin sesión | **Implementada y vigente en lo esencial** — el Sprint 4 añadió los POST del Kill-Switch; el cierre de sesión/roles queda en CR-006 |
| **CR-005** | Kill-Switch: `kill_switch_state`, columna `actor`, inmutabilidad de `audit_logs`, broker simulado, RNF-010 | **Implementada y verificada** contra PostgreSQL real — **pendiente de aprobación formal** |
| **CR-006** | Autenticación mínima: sin usuarios ni roles, reinicio con clave, brechas RF-503 / RNF-001 / RNF-005 | **Brechas declaradas, no cerradas** — **pendiente de aprobación formal** |
| **CR-007** | FASE D: reportes (RF-702) y notificaciones (SS-11) — qué se implementó y qué no | **Implementado y verificado en vivo** (Mailpit y PostgreSQL reales) — **pendiente de aprobación formal** |
| **CR-008** | Visor de auditoría abierto sin autenticación en la intranet (ampliación de la brecha de CR-006) | **Brecha declarada, no cerrada** — en un despliegue real iría detrás de login/TOTP |

---

## 5. Cómo se verificó esto

```bash
docker compose up -d postgres
uv run --frozen --group ai --group ai-cpu pytest -q   # 958 pruebas (20 vivas se omiten sin contenedor)
cd frontend && npm test && npm run lint && npm run build   # 61 pruebas
docker compose stop postgres
```

Las evidencias citadas apuntan a archivo, test, commit o informe; las que
dependen de una observación **pendiente** lo dicen en la columna de
brecha. Esta matriz se revisa al cierre de cada sprint.
