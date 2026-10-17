# AGENTS.md — RiTech-NASDAQ100

Contexto persistente para cualquier agente de codificación (OpenCode, Claude Code, etc.) que trabaje en este repositorio. Léelo completo antes de generar o modificar código.

**Este archivo es un resumen condensado.** Los documentos fuente completos y aprobados (v1.0) están en:
- `docs/srs.md` — Software Requirements Specification completo (todos los RF-XXX, RNF-XXX, REST-XXX con su texto exacto)
- `docs/sdd.md` — Software Design Description completo (esquema físico de BD, UML, clases detalladas)
- `docs/backlog.md` — Product Backlog y planificación de sprints
- `docs/change-requests.md` — Registro de cambios formales a SRS/SDD (CR-001: campos `price_high`/`price_low`; CR-002: proveedor de datos Alpha Vantage → Twelve Data, RF-102 parcialmente cubierto)

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

Flujo: `Alpha Vantage API → IngestionEngine (M2) → InfluxDB (M2) → LocalPredictiveModel (M1, CUDA) → RiskManager/AsymmetricCalculator (M2) → Interactive Brokers Gateway (M2, TCP local)`.

## 4. Stack tecnológico

- **Backend:** Python 3.12, FastAPI, Pydantic estricto, `psycopg2` (PostgreSQL síncrono), `influxdb-client` (async), Docker Compose.
- **Frontend:** React + Vite + Tailwind CSS + shadcn/ui, TypeScript estricto, Zod + React Hook Form para validación, Recharts para gráficos.
- **IA:** PyTorch con NVIDIA CUDA v12+.
- **Bases de datos:** PostgreSQL 16 (relacional/auditoría), InfluxDB 2.7 (series temporales, lenguaje Flux).
- **Broker:** Interactive Brokers Gateway (TWS API) vía TCP local, cuenta Sandbox en desarrollo/Sprint 4.
- **Datos de mercado:** Alpha Vantage API (HTTP asíncrono).
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
| `IngestionEngine` | `src/ingestion/engine.py` | Consume Alpha Vantage cada 5 min, valida payload, escribe en InfluxDB | RF-101, RF-102, RF-103 |
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
- **Seguridad:** ningún dato sale del hardware local; credenciales de APIs (IB, Alpha Vantage) encriptadas en reposo con **AES-256**.
- **Fiabilidad:** ante caída de red, suspende nuevas órdenes, alerta al operador, y al reconectar evita duplicar transacciones.
- **Usabilidad:** acciones críticas (login, activar Modo Autónomo, Kill-switch) a máximo 2 toques. Estados visuales: verde (normal), amarillo (inestable), rojo parpadeante (Kill-switch activo).

## 14. Estado actual del proyecto

- **Sprint 1 (Planeación) — COMPLETADO.** SRS v1.0 y SDD v1.0 aprobados (este archivo se basa en ellos).
- **Sprint 2 (Walking Skeleton) — siguiente foco de desarrollo:**
  - Ítem 4: Config. de red local + Docker Compose (PostgreSQL 16, InfluxDB 2.7) + encriptación AES-256 de credenciales.
  - Ítem 5: `IngestionEngine` para velas de 5M contra Alpha Vantage.
  - Ítem 6: `AsymmetricCalculator` (versión virtual/simulada).
- **Sprint 3:** modelo predictivo CUDA local + Dashboard React.
- **Sprint 4:** Kill-switch, auditoría 5 años, notificaciones SMS/SMTP, integración TCP con IB, reportes.

## 15. Glosario

- **TWS API:** interfaz local de Interactive Brokers para enviar/recibir órdenes vía TCP.
- **Modo Sombra:** simulación con datos reales sin comprometer capital real.
- **Kill-Switch:** protocolo que cierra todas las operaciones y bloquea el sistema ante pérdidas severas o fallos.
- **XAI:** explicabilidad de las decisiones de la IA para fines de auditoría.
