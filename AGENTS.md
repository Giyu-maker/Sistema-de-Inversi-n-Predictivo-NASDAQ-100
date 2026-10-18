# AGENTS.md — RiTech-NASDAQ100

Contexto persistente para cualquier agente de codificación (OpenCode, Claude Code, etc.) que trabaje en este repositorio. Léelo completo antes de generar o modificar código.

**Este archivo es un resumen condensado.** Los documentos fuente completos y aprobados (v1.0) están en:
- `docs/srs.md` — Software Requirements Specification completo (todos los RF-XXX, RNF-XXX, REST-XXX con su texto exacto)
- `docs/sdd.md` — Software Design Description completo (esquema físico de BD, UML, clases detalladas)
- `docs/backlog.md` — Product Backlog y planificación de sprints
- `docs/change-requests.md` — Registro de cambios formales a SRS/SDD (CR-001: campos `price_high`/`price_low`; CR-002: proveedor de datos Alpha Vantage → Twelve Data, RF-102 parcialmente cubierto; **CR-003: inmutabilidad del histórico en InfluxDB, resuelta con guarda insert-if-absent en la capa de aplicación**)

Cuando una tarea requiera el texto exacto de un requisito, una firma de clase completa, o cualquier detalle no cubierto abajo, **lee el archivo fuente correspondiente en `docs/` antes de asumir nada.** Este resumen puede quedar desactualizado si el SRS/SDD se revisan; los archivos en `docs/` son la fuente de verdad.

## 1. Qué es el proyecto

**Sistema de Inversión Predictivo NASDAQ-100 (RiTech-NASDAQ100)** — software de trading algorítmico e IA para uso exclusivo de **RiTech SAS**, desarrollado por **Naranja's Team**. Automatiza el monitoreo del índice NASDAQ-100, predice su comportamiento con un modelo entrenado localmente y ejecuta/asiste decisiones de inversión bajo una lógica de ganancias y pérdidas asimétrica. Reemplaza el proceso manual actual (una secretaria vigilando precios visualmente).

**Equipo:**
| Persona | Rol |
|---|---|
| Javier Martínez | Arquitecto líder & DevOps local. Diseño de la red del clúster, despliegue Docker en Máquina 2, arquitectura del Kill-switch, SDD |
| Javier Martínez / Dylan Merchancano | Especialista en IA / Backend. Entrenamiento PyTorch+CUDA en Máquina 1, XAI, motor asimétrico, SRS |
| Dylan Merchancano | Frontend & integración API. Dashboard React en Máquina 3, ingesta de datos, conexión TCP a IB Sandbox |
| Aida Liliana Rosero Ruiz | Gerente General / Sponsor. Única autorizada para pasar de Modo Asesor a Modo Autónomo, define perfiles de riesgo |

## 2. Restricciones no negociables (léelas antes de proponer cualquier solución)

- **REST-01 — Cero nube.** Nada de AWS, Azure, GCP ni ningún servicio cloud equivalente. Todo corre on-premise en 3 máquinas físicas de la oficina.
- **REST-02 — Confidencialidad de la IA.** Prohibido enviar datos financieros reales a APIs de IA externas (OpenAI, Anthropic, etc.). El entrenamiento e inferencia del modelo predictivo del sistema debe ser 100% local (GPU de la Máquina 1). *Esto no aplica a la herramienta de codificación que uses para desarrollar — esa es un asunto aparte, pero nunca le pegues credenciales reales ni datos de mercado reales.*
- **REST-03 — Presupuesto.** Máximo $20.000.000 COP para desarrollo e infraestructura física.
- **REST-04 — Fecha límite.** Producción a más tardar a principios de noviembre de 2026, improrrogable.
- **REST-05 — Residencia de datos.** Toda la información transaccional y de clientes debe permanecer físicamente en Colombia.
- **REST-06 — Pentesting.** Las pruebas de penetración corren por cuenta técnica y económica de Naranja's Team, en la fase final de despliegue (noviembre 2026).

## 3. Arquitectura física — clúster de intranet on-premise (3 máquinas)

| | Máquina 1 — Nodo IA | Máquina 2 — Nodo Central | Máquina 3 — Estación Operador |
|---|---|---|---|
| Responsable | Javier Martínez | Javier Martínez | Dylan Merchancano |
| Hardware | ASUS PRIME B450M-A II, Ryzen 5 3600 (6c/12h @ 4.21GHz), 15.52 GB RAM, 1TB Btrfs | Board 8CAD, Intel Core 5 120U (12c/16h @ 5.00GHz), 30.99 GB RAM, 500GB Btrfs | ASUSTeK Vivobook X1605VA, i9-13900H (14c/20h @ 2.60GHz), 23.64 GB RAM, 1TB SSD NTFS |
| GPU | NVIDIA GTX 1660 SUPER (discreta, CUDA v12+) | Intel Graphics (integrada) | Intel Iris Xe (integrada) |
| SO | CachyOS x86_64 (kernel 7.1.2-3-cachyos) | CachyOS x86_64 (kernel 7.2.0-1-cachyos) | Windows 11 Pro 64-bit (Build 26200) |
| Rol | Servidor de inferencia: PyTorch CUDA, entrenamiento diario asíncrono del modelo predictivo ligero | Nodo maestro backend + BD: Docker con PostgreSQL v16, InfluxDB v2.7, FastAPI (Python 3.12), IB Gateway | Terminal del operador/gerente y servidor frontend: Dashboard React+Vite (o servido vía Nginx desde Máquina 2) |

Flujo: `Twelve Data API (QQQ) → IngestionEngine (M2) → InfluxDB (M2) → LocalPredictiveModel (M1, CUDA) → RiskManager/AsymmetricCalculator (M2) → Interactive Brokers Gateway (M2, TCP local)`.

## 4. Stack tecnológico

- **Backend:** Python 3.12, FastAPI, Pydantic estricto, `psycopg2` (PostgreSQL síncrono), `influxdb-client` (async), Docker Compose.
- **Frontend:** React + Vite + Tailwind CSS + shadcn/ui, TypeScript estricto, Zod + React Hook Form para validación, Recharts para gráficos.
- **IA:** PyTorch con NVIDIA CUDA v12+.
- **Bases de datos:** PostgreSQL 16 (relacional/auditoría), InfluxDB 2.7 (series temporales, lenguaje Flux).
- **Broker:** Interactive Brokers Gateway (TWS API) vía TCP local, cuenta Sandbox en desarrollo/Sprint 4.
- **Datos de mercado:** Twelve Data API (HTTP asíncrono), símbolo `QQQ` como proxy del NASDAQ-100, tras CR-002. Interfaz `MarketDataProvider` intercambiable. *(El SRS y el SDD v1.0 aún nombran Alpha Vantage: cambio pendiente de aprobación formal — ver CR-002.)*
- **Notificaciones:** módem GSM por USB serie o SMTP/SMS local; correo vía SMTP local.

## 5. Convenciones de código obligatorias

1. **Tipado estricto siempre.** TypeScript estricto en frontend, Pydantic estricto en cada endpoint FastAPI. `any` está prohibido salvo justificación aprobada por el líder de arquitectura (RNF-011).
2. **IDs.** Todo registro en PostgreSQL usa UUID generado localmente: `crypto.randomUUID()` en frontend, `uuid.uuid4()` en backend. Nunca IDs secuenciales predecibles.
3. **Auditoría obligatoria.** Ninguna clase de negocio puede ejecutar trading ni cambiar perfiles de riesgo sin insertar síncronamente su log correspondiente en `audit_logs`, incluyendo la IP interna de la intranet.
4. **Estructura de carpetas:** `src/ingestion/`, `src/calculator/`, `src/ai/`, `src/security/`, `src/integration/`, `src/database/`, `src/utils/` (backend); `components/`, `hooks/`, `pages/` (frontend).
5. **Idioma de la interfaz:** español neutro, exclusivamente. Mensajes de error inline claros.
6. **Formato de fecha:** `DD/MM/YYYY HH:mm:ss`. Operaciones registradas en USD; reportes convertibles a COP con tasa fija o actualizada manualmente.

## 6. Motor de lógica asimétrica (regla de negocio central)

- **Ganancia:** el rendimiento de RiTech se calcula como `variación_NASDAQ_% × 2.0` cuando la variación es positiva (apalancamiento 2x).
- **Pérdida:** limitada a un máximo teórico de **1.25%** de la inversión de la sesión en condiciones normales de mercado.
- **Gaps de mercado:** si el índice cae ≥2% de golpe (sin operaciones intermedias), se registra la pérdida real aunque supere el 1.25% teórico, y se informa/justifica la diferencia al usuario.
- **Ventanas de exclusión:** el motor no abre posiciones nuevas durante los primeros ni los últimos 15 minutos del horario regular de mercado (alta volatilidad).
- Referencia de implementación (`src/calculator/asymmetric.py`):
  ```python
  class AsymmetricCalculator:
      @staticmethod
      def calculate_yield(nasdaq_variation_pct: float) -> float: ...
      @staticmethod
      def is_threshold_breached(entry_price: float, current_price: float) -> bool: ...
  ```

## 7. IA predictiva local

- Entrenamiento **exclusivamente local**, sobre la GPU de la Máquina 1. Prohibida cualquier transmisión de datos fuera del servidor.
- Reentrenamiento automático diario, al cierre de cada jornada bursátil (después de las 8:00 PM EST).
- Cada predicción debe incluir explicabilidad técnica (XAI) en lenguaje financiero, más un **ratio de confianza (0–100%)**.
- Si la confianza es **menor al 75%**, el sistema NO abre operaciones automáticas en Modo Autónomo.
- Salida esperada de `run_inference`: `(trend: 'UP'|'DOWN'|'STABLE', confidence: float, rationale: str)`.

## 8. Modos de operación y perfiles de riesgo

- **Modo Asesor** (por defecto): la IA solo recomienda; un humano ejecuta manualmente.
- **Modo Autónomo**: requiere autorización explícita de la Gerente General + autenticación multifactor local (OTP). El sistema opera y envía órdenes sin intervención humana.
- **Perfiles de riesgo** y umbral de confianza mínimo requerido para operar:
  | Perfil | Confianza mínima |
  |---|---|
  | CONVENTIONAL (Conservador) | 0.85 |
  | MODERATE (Moderado) | 0.75 (por defecto) |
  | AGGRESSIVE (Agresivo) | 0.65 |

## 9. Kill-Switch maestro (seguridad crítica)

- Un único botón de emergencia, accesible en máximo 2 toques desde el Dashboard.
- Al activarse (manual o automáticamente al cruzar -1.25% de pérdida diaria): **prioridad de hilos de CPU**, bloquea síncronamente cualquier otro cálculo, congela nuevas órdenes, y liquida (vende) inmediatamente **todas** las posiciones abiertas al precio de mercado vía TCP a Interactive Brokers.
- Tras dispararse, el sistema rechaza toda orden/sugerencia nueva hasta que un **Administrador de TI** o el **Gerente** restablezca la configuración explícitamente.

## 10. Modo Sombra (Shadow Mode)

- Usa datos reales en tiempo real pero envía órdenes ficticias a una cuenta **Sandbox** de Interactive Brokers, sin comprometer capital.
- Toda orden simulada se guarda en `transactions` con `is_simulated = true`, separada estrictamente de auditorías reales.
- El cliente debe proveer credenciales de la cuenta Sandbox para verificar este modo en el Sprint 4.

## 11. Modelo de datos

**PostgreSQL** (relacional, retención mínima **5 años**):

- `trader_users`: `id UUID [PK]`, `username VARCHAR(50) UNIQUE`, `password_hash VARCHAR(255)` (Bcrypt), `role VARCHAR(20)` (`ADMIN_TI`|`OPERATOR`|`GERENTE`), `totp_secret VARCHAR(100)`.
- `system_configurations`: `id UUID [PK]`, `max_loss_day DECIMAL(5,2) DEFAULT 1.25`, `is_autonomous BOOLEAN DEFAULT FALSE`, `risk_profile VARCHAR(20) DEFAULT 'CONVENTIONAL'`.
- `ai_predictions`: `id UUID [PK]`, `prediction_time BIGINT` (epoch ms), `trend_prediction VARCHAR(10)` (`UP`|`DOWN`|`STABLE`), `confidence_score DECIMAL(5,4)`, `technical_rationale TEXT`.
- `transactions`: `id UUID [PK]`, `prediction_id UUID [FK -> ai_predictions.id, NULLABLE]`, `broker_order_id VARCHAR(100)`, `transaction_type VARCHAR(10)` (`BUY`|`SELL`), `quantity DECIMAL(12,4)`, `execution_price DECIMAL(12,4)`, `execution_time BIGINT`, `is_simulated BOOLEAN DEFAULT TRUE`.
- `audit_logs`: `id UUID [PK]`, `timestamp BIGINT`, `user_id UUID [FK -> trader_users.id, NULLABLE]`, `action VARCHAR(100)` (ej. `KILL_SWITCH_TRIGGERED`, `MODE_AUTONOMOUS_ENABLED`), `ip_address VARCHAR(45)`, `payload TEXT` (JSON).

**InfluxDB** (series temporales, velas de 5 min, sin escrituras destructivas sobre histórico):

- `NasdaqTick`: `time` (epoch ms), `price_open`, `price_close`, `price_high`, `price_low`, `volume` (Float).

## 12. Módulos backend y su responsabilidad

| Módulo | Archivo | Responsabilidad | Requisitos |
|---|---|---|---|
| `IngestionEngine` | `src/ingestion/engine.py` | Consume Twelve Data vía `MarketDataProvider` cada 5 min, valida payload, escribe en InfluxDB con guarda de inmutabilidad (CR-003) | RF-101, RF-102, RF-103 |
| `AsymmetricCalculator` | `src/calculator/asymmetric.py` | Cálculo de rendimiento 2x / pérdida 1.25% / gaps | RF-201, RF-202, RF-203 |
| `LocalPredictiveModel` | `src/ai/model.py` | Carga y corre el modelo PyTorch en GPU local, genera predicción + XAI + confianza | RF-301, RF-302, RF-303 |
| `RiskManager` | `src/security/risk_manager.py` | Valida umbrales por perfil de riesgo, dispara el Kill-switch | RF-402, RF-403, RNF-010 |
| `KillSwitchEngine` | `src/security/kill_switch.py` | Liquidación síncrona de posiciones | RF-501, RF-502, RF-503 |
| `BrokerGateway` | `src/integration/broker.py` | Comunicación TCP con IB Gateway (real y Sandbox) | RF-402, RF-601 |
| `ExportUtility` | `src/utils/export.py` | Reportes PDF/CSV locales | RF-702 |
| Dashboard | `src/pages/Dashboard.tsx` | UI: selector de modo, Kill-switch, velas 5M | RF-401, RF-501 |

## 13. Requisitos no funcionales clave (ISO/IEC 25010)

- **Rendimiento:** procesar una vela nueva + IA + cálculo asimétrico + actualizar UI en **< 500 ms**. Carga inicial del frontend **< 1.5 s**.
- **Disponibilidad:** **99.9%** durante horario extendido (4:00 AM–8:00 PM EST, L–V). Mantenimientos solo después de las 6:00 PM hora Colombia.
- **Seguridad:** ningún dato sale del hardware local; credenciales de APIs (IB, Twelve Data) encriptadas en reposo con **AES-256**.
- **Fiabilidad:** ante caída de red, suspende nuevas órdenes, alerta al operador, y al reconectar evita duplicar transacciones.
- **Usabilidad:** acciones críticas (login, activar Modo Autónomo, Kill-switch) a máximo 2 toques. Estados visuales: verde (normal), amarillo (inestable), rojo parpadeante (Kill-switch activo).

## 14. Estado actual del proyecto

- **Sprint 1 (Planeación) — COMPLETADO.** SRS v1.0 y SDD v1.0 aprobados (este archivo se basa en ellos).
- **Sprint 2 (Walking Skeleton) — COMPLETADO (cierre 17/10/2026, tag `sprint-2-cierre`).**
  - Ítem 4: Docker Compose con PostgreSQL 16 e InfluxDB 2.7 en M2, ambos `healthy` y expuestos **solo en loopback** (`127.0.0.1`), más encriptación AES-256-GCM de credenciales en reposo (`src/security/crypto.py`).
  - Ítem 5: `IngestionEngine` con cadencia de 5 min, validación estricta Pydantic y escritura en InfluxDB.
  - Ítem 6: `AsymmetricCalculator` (2x en ganancias, 1.25% en pérdidas, detección de gaps).
  - **Proveedor de datos: Twelve Data (plan gratuito *Basic*) con el símbolo `QQQ`** como proxy del NASDAQ-100, tras el cambio registrado en CR-002. **Limitación de alcance aceptada y documentada:** Basic solo cubre horario regular (09:30–16:00 ET, 78 velas de las ~192 del escenario RF-101); RF-102 queda **parcialmente cubierto**. El HTTP pasa por la interfaz **`MarketDataProvider`** (`src/ingestion/providers/base.py`), por lo que cambiar de fuente solo toca un adaptador.
  - **Hallazgos de cierre, registrados en `docs/change-requests.md`:** CR-001 (`price_high`/`price_low` en la firma SDD), CR-002 (cambio de proveedor) y **CR-003 — inmutabilidad del histórico**: InfluxDB 2.7 **sobrescribe en silencio** un punto con el mismo timestamp, así que RF-103/SRS §4.4 no lo garantiza el motor. Se resolvió en la capa de aplicación con una guarda **insert-if-absent** en `write_to_influx()` (no escribe + `WARNING` si el timestamp existe) y con `existing_timestamps()`/`write_candles_batch()` para el backfill (una consulta de rango y una escritura por lote). Verificado contra InfluxDB real.
  - **RNF-003 reclasificado a "no verificable aún"**: la medición del Sprint 2 (540–1116 ms con fetch externo; 6–59 ms de proceso local) no cubre el alcance del SRS §6.2, que además requiere inferencia de IA y UI. Se re-mide al cierre de Sprint 3.
- **Sprint 3 — EN CURSO (Fase A: Ítem 7, red neuronal predictiva local).**
  - Desarrollo y pruebas ocurren en **M2**; el código que correrá en M1 (IA, CUDA) y M3 (operador) se construye aquí y se despliega después. Ver sección 15.
  - Fase A: features/dataset sin fuga de datos, `LocalPredictiveModel` (MLP pequeño), XAI por gradiente×entrada, ratio de confianza, reentrenamiento diario por CLI, tabla `ai_predictions`, y el límite M2→M1 (`train` + `serve` en FastAPI).
  - Fase B (después): API de M2 para el dashboard, dashboard React, Kill-switch, IB.
- **Sprint 4:** Kill-switch, auditoría 5 años, notificaciones SMS/SMTP, integración TCP con IB, reportes.

## 15. Modelo de desarrollo y despliegue

Aplica a Sprint 3 y posteriores. Objetivo: **en la otra máquina solo haya que instalar dependencias y arrancar.**

1. **Configuración 100% por variables de entorno**, con un `.env.<rol>.example` por máquina (`.env.m1.example`, `.env.m2.example`, `.env.m3.example`). Nada de `localhost` ni IPs escritas en el código: las URLs de servicios (`INFERENCE_URL`, `INFLUX_URL`, `INFLUXDB_URL`, …) se leen del entorno.
2. **Mínimo privilegio:** cada máquina recibe solo los secretos que necesita. **M1 no recibe la API key de Twelve Data ni credenciales de PostgreSQL** — solo el token de lectura de InfluxDB y el artefacto del modelo.
3. **Dependencias por rol** en `pyproject.toml` mediante grupos (`base`, `ai`, `dev`) con lockfile versionado. `torch` vive **solo** en el grupo `ai`: M1 (CUDA) y M2 (CPU) instalan el índice de PyTorch que corresponde a su hardware. Instrucciones exactas en `docs/despliegue.md`.
4. **Artefactos portables:** se guarda el `state_dict` y se carga con `map_location`, de modo que un modelo entrenado en CPU (M2) carga en CUDA (M1) y a la inversa.
5. **Sin comandos ni rutas específicas de SO en el código** — se usa `pathlib`. Las unidades systemd y los scripts de shell viven en `deploy/`, no se mezclan con la lógica.

### Límite de despliegue M2 → M1

- **M1 ejecuta dos entrypoints de `src/ai`:** `train` (CLI de reentrenamiento, RF-302) y `serve` (servicio FastAPI mínimo con `/health` y `/predict`, que recibe el tensor de features y devuelve `(trend, confidence, rationale)`).
- **M1 NO escribe en PostgreSQL.** Las predicciones las persiste M2 en `ai_predictions`.
- **El cliente de inferencia vive en M2** y apunta a `INFERENCE_URL`. En desarrollo apunta a localhost; mismo código en despliegue, solo cambia la configuración.
- **FastAPI se permite únicamente** para este servicio de inferencia en esta fase. La API de M2 para el dashboard es Fase B.

## 16. Glosario

- **TWS API:** interfaz local de Interactive Brokers para enviar/recibir órdenes vía TCP.
- **Modo Sombra:** simulación con datos reales sin comprometer capital real.
- **Kill-Switch:** protocolo que cierra todas las operaciones y bloquea el sistema ante pérdidas severas o fallos.
- **XAI:** explicabilidad de las decisiones de la IA para fines de auditoría.

## 17. Herramientas de diseño (Fase B)

Herramientas de dirección visual y QA de UI instaladas a nivel de **proyecto** (nada global) para el dashboard de la Fase B. El código de terceros vive en carpetas ignoradas por git; solo se versionan los metadatos (`skills-lock.json`) y esta documentación.

### 17.1 Qué hay instalado y dónde

| Herramienta | Ruta | Notas |
|---|---|---|
| Skills de diseño (8) | `.agents/skills/` | `find-skills`, `emil-design-eng`, `review-animations`, `animation-performance`, `break-ui`, `pick-ui-library`, `minimalist-ui`, `full-output-enforcement` |
| Impeccable | `.opencode/skills/impeccable/` + `.opencode/commands/impeccable.md` | v4.6.0, con binario del motor v0.1.15 en `scripts/bin/linux-x64/` |
| Figma (plugin OpenCode) | global (`opencode plugin add`) | Plugin oficial; OAuth propio, ver §17.3 |
| Lockfile de skills | `skills-lock.json` | Metadatos (fuente + hash) para reinstalar; se versiona |

### 17.2 Comandos de reinstalación

Todo se instala desde la raíz del proyecto. La CLI de skills detecta el proyecto y usa alcance local por defecto.

```bash
# Skills (verificar nombres con --list antes de instalar)
npx skills add vercel-labs/skills --skill find-skills --agent opencode -y
for s in emil-design-eng review-animations animation-performance break-ui pick-ui-library; do
  npx skills add emilkowalski/skills --skill "$s" --agent opencode -y
done
for s in minimalist-ui full-output-enforcement; do
  npx skills add https://github.com/Leonxlnx/taste-skill --skill "$s" --agent opencode -y
done

# Impeccable (proceso/QA de diseño). NO ejecutar /impeccable init sin autorización de la Gerente.
npx impeccable install --providers=opencode --scope=project
npx impeccable telemetry off        # configurado en ~/.impeccable/config.json

# Plugin oficial de Figma para OpenCode (requiere opencode >= 2.0.29)
opencode plugin add 'git+https://github.com/anomalyco/opencode.git#path:packages/plugins/figma'
```

Restaurar solo desde el lockfile: `npx skills experimental_install`. Actualizar: `npx skills update`.

### 17.3 Reglas de uso

1. **El SRS/SDD manda sobre cualquier skill.** UI en español neutro; fechas `DD/MM/YYYY HH:mm:ss`; modo oscuro/claro; stack React + Vite + Tailwind + shadcn/ui + Recharts; Zod + React Hook Form; estados verde (normal) / amarillo (inestable) / rojo parpadeante (Kill-switch activo); acciones críticas (login, Modo Autónomo, Kill-switch) a máximo 2 toques; responsive. Si una skill contradice un requisito, gana el requisito.
2. **Una sola dirección visual a la vez.** `minimalist-ui` para dirección de estilo + `impeccable` para proceso/QA. No cargar otras skills de dirección de arte (en particular ninguna del repo `taste-skill` fuera de las dos instaladas).
3. **Movimiento mínimo y funcional.** El Kill-Switch y sus estados nunca llevan animaciones que retrasen la acción u oculten el estado. `review-animations` y `animation-performance` son para revisar, no para añadir motion decorativo.
4. **`break-ui` con datos extremos:** ejecutarla sobre cada pantalla antes de darla por terminada (historial vacío, números largos, predicción nula, API caída, textos no latinos).
5. **`pick-ui-library`:** solo antes de añadir cualquier librería de UI. Por defecto la respuesta es shadcn/ui.
6. **`full-output-enforcement`:** solo si la salida generada se corta.
7. **`find-skills`:** solo si la Gerente o el líder la pide explícitamente.
8. **Figma:** el login (OAuth) lo hace la persona usuaria en su sesión; no se guardan `client_id`/`client_secret` ni URLs de endpoint a mano — el plugin los resuelve. No commitear tokens.
9. **Figma en modo solo lectura por defecto.** El plan puede tener cupo bajo de llamadas, así que:
   - Para **implementar diseños** se usa la vía de lectura: skill `figma-design-to-code` + `get_design_context` (y `get_screenshot`/`get_metadata` si hace falta). Cargar la skill **antes** de la herramienta: es requisito previo obligatorio.
   - **NO ejecutar** `use_figma`, `create_new_file`, `generate_figma_design`, `send_code_to_figma`, `html_to_figma`, `upload_assets`, `export_video`, `generate_image`, ni ninguna herramienta que **escriba** en Figma, salvo petición explícita de la Gerente o el líder **en el mismo mensaje**.
   - Antes de invocar **cualquier** herramienta de Figma, informar en el chat cuántas llamadas se harían y para qué, y esperar visto bueno.
   - No usar herramientas `weave_*` ni de shaders/plugins generativos: quedan fuera del alcance de la Fase B.
