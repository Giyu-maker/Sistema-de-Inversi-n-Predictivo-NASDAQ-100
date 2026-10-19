# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Audiencia interna única de RiTech SAS, en una sola terminal (Máquina 3, Windows 11):

- **Operador** (rol `OPERATOR`, persona: Dylan Merchancano): vive la interfaz durante la jornada bursátil. Su trabajo: monitorear las predicciones de la IA local, evaluar el ratio de confianza y la explicación (XAI), decidir y ejecutar órdenes en Modo Asesor, o supervisar la ejecución automática en Modo Autónomo, y disparar el Kill-Switch ante pérdidas severas.
- **Gerente General** (rol `GERENTE`, persona: Aida Liliana Rosero Ruiz): misma terminal, con autoridad exclusiva para habilitar el Modo Autónomo (OTP) y definir los perfiles de riesgo.
- Rol secundario confirmado: **Administrador de TI** (`ADMIN_TI`, persona: Javier Martínez): restablece el sistema tras un Kill-Switch y administra la configuración; no es usuario diario de la interfaz de trading.

Situación: monitoreo activo dentro del horario extendido de mercado (4:00 AM–8:00 PM EST, L–V), a menudo en momentos de alta volatilidad (apertura/cierre). El producto reemplaza un proceso 100% manual (una persona vigilando precios visualmente).

## Product Purpose

**Sistema de Inversión Predictivo NASDAQ-100 (RiTech-NASDAQ100)**: software de trading algorítmico e IA que automatiza el monitoreo del índice NASDAQ-100 (proxy QQQ), predice su comportamiento con un modelo entrenado localmente y ejecuta/asiste decisiones de inversión bajo una lógica de ganancias y pérdidas asimétrica.

Éxito del producto: operar con disponibilidad 99.9% en horario extendido; procesar cada vela nueva (dato → inferencia → cálculo asimétrico → UI) en menos de 500 ms; mantener la pérdida diaria techo en 1.25%; y estar en producción a más tardar a principios de noviembre de 2026 (REST-04, improrrogable). Hoy el backend (Sprints 1–2) está completo y el Sprint 3 (IA local + dashboard) está en curso.

## Positioning

El mecanismo que un producto vecino no podría copiar sin mentir:

- **100% on-premise, cero nube** (REST-01): todo corre en un clúster de intranet de 3 máquinas físicas en la oficina; ninguna información sale del hardware local y toda la data transaccional permanece en Colombia (REST-05).
- **IA confidencial y local** (REST-02): entrenamiento e inferencia exclusivamente en la GPU de la Máquina 1; prohibido enviar datos financieros reales a APIs de IA externas.
- **Motor asimétrico propio**: rendimiento = variación NASDAQ-% × 2.0 en ganancias; pérdida acotada a 1.25% de la inversión de sesión, con detección y justificación de gaps (caídas ≥2% de golpe).
- **Kill-Switch maestro**: un único botón de emergencia, a máximo 2 toques, que liquida sincrónicamente todas las posiciones vía TCP y bloquea el sistema hasta reset explícito.

## Operating Context

- **Clúster on-premise de 3 máquinas**: M1 Nodo IA (Ryzen 5 3600 + GTX 1660 SUPER, CUDA, CachyOS), M2 Nodo Central (PostgreSQL 16, InfluxDB 2.7, FastAPI, IB Gateway, Docker, CachyOS), M3 Estación Operador (terminal Windows 11, dashboard React).
- **Flujo de datos**: Twelve Data API (QQQ) → IngestionEngine (M2, velas cada 5 min) → InfluxDB → LocalPredictiveModel (M1, CUDA, reentrenamiento diario al cierre, >8:00 PM EST) → RiskManager/AsymmetricCalculator (M2) → IB Gateway (M2, TCP local).
- **Datos de mercado**: Twelve Data plan Basic (tras CR-002, antes Alpha Vantage). Limitación aceptada y documentada: Basic solo cubre horario regular (09:30–16:00 ET), por lo que RF-102 queda parcialmente cubierto. Interfaz `MarketDataProvider` intercambiable.
- **Broker**: Interactive Brokers Gateway vía TCP local. Desarrollo y Sprint 4 en cuenta **Sandbox** (Modo Sombra, `is_simulated = true`); el cliente debe proveer credenciales Sandbox para verificación.
- **Ritmos**: mantenimientos solo después de las 6:00 PM hora Colombia; operaciones registradas en USD; reportes convertibles a COP con tasa fija o actualizada manualmente; formato de fecha siempre `DD/MM/YYYY HH:mm:ss`.
- **Modelo de desarrollo**: el código se construye en M2 y se despliega a M1/M3; configuración 100% por variables de entorno (`.env.m1/m2/m3.example`), mínimo privilegio por máquina, dependencias por rol (`pyproject.toml` con grupos `base`/`ai`/`dev`).

## Capabilities and Constraints

**Capacidades confirmadas (estado actual y planificado):**

- Modos de operación: **Modo Asesor** (default; la IA solo recomienda, un humano ejecuta) y **Modo Autónomo** (requiere autorización explícita de la Gerente General + OTP local).
- Perfiles de riesgo con umbral de confianza mínimo: CONVENTIONAL 0.85, MODERATE 0.75 (default), AGGRESSIVE 0.65. Confianza < 75% ⇒ sin operaciones automáticas.
- Predicción con XAI en lenguaje financiero + ratio de confianza 0–100% (`trend: UP|DOWN|STABLE, confidence, rationale`).
- Motor asimétrico (2x ganancia / 1.25% pérdida / detección de gaps), ventanas de exclusión de ±15 min de apertura/cierre.
- Kill-Switch (manual o automático al cruzar −1.25% diario) con liquidación total y bloqueo posterior.
- Auditoría obligatoria en `audit_logs` (acción, usuario, IP interna, payload JSON), retención mínima de 5 años.
- Exportación de reportes PDF/CSV locales (Sprint 4).

**Restricciones no negociables:** REST-01 cero nube; REST-02 IA 100% local; REST-03 presupuesto máximo $20.000.000 COP; REST-04 producción nov 2026; REST-05 residencia de datos en Colombia; REST-06 pentesting por cuenta de Naranja's Team (nov 2026).

**Convenciones de interfaz ya comprometidas (SRS/SDD, no negociables):** UI en **español neutro exclusivo** con mensajes de error inline claros; fecha `DD/MM/YYYY HH:mm:ss`; estados visuales verde (normal) / amarillo (inestable) / rojo parpadeante (Kill-Switch activo); acciones críticas (login, Modo Autónomo, Kill-switch) a máximo 2 toques; carga inicial del frontend < 1.5 s; tipado estricto (TypeScript estricto, Pydantic estricto; `any` prohibido sin aprobación); IDs UUID locales, nunca secuenciales. Stack de frontend fijado por SRS/SDD: React + Vite + Tailwind CSS + shadcn/ui, TypeScript estricto, Zod + React Hook Form, Recharts.

**Hechos abiertos (decisión pendiente, registrado sin inventar):**

- Dónde se sirve el dashboard en producción: en M3 directamente o vía Nginx desde M2 (ambas opciones quedan abiertas en AGENTS.md §3).
- Si se exige cubrir RF-102 completo (pre/post-market), hará falta otro proveedor detrás de `MarketDataProvider`.
- El frontend aún no existe como código; es la Fase B del Sprint 3.

## Brand Commitments

- Nombre propio obligatorio: **RiTech** / **RiTech SAS**; el equipo desarrollador se llama **Naranja's Team**.
- **No existen activos de marca gráficos** (confirmado por el usuario): no hay logo, manual de marca ni tipografías obligatorias. El trabajo visual puede partir de cero, siempre conservando el nombre.
- Voz comprometida: español neutro, sobrio y operativo; sin lenguaje promocional (es un producto interno, no una página de marketing) y sin claims que el sistema no pueda respaldar con datos.
- La confidencialidad es identidad: ninguna copy ni interfaz puede sugerir o requerir envío de datos externos.

## Evidence on Hand

- `AGENTS.md` — contexto persistente del equipo (roles, máquinas, restricciones, motor asimétrico, modelo de datos).
- `docs/srs.md` — SRS v1.0 con todos los RF/RNF/REST (fuente de verdad de requisitos).
- `docs/sdd.md` — SDD v1.0: esquema físico de BD, UML, clases.
- `docs/backlog.md`, `docs/change-requests.md` (CR-001, CR-002, CR-003), `docs/despliegue.md`, `docs/checklist_m2.md`, `docs/plan-ia-sprint3.md`, `docs/informe-entrenamiento-sprint3.md`.
- Backend real en `src/` (`ai/`, `calculator/`, `database/`, `ingestion/`, `security/`), `docker-compose.yml`, `.env.m1/m2/m3.example`.

**Ausencias que ningún trabajo futuro debe fabricar:** no hay logo ni activos visuales; no existe todavía ninguna interfaz ni capturas de pantalla del producto; no hay datos reales de mercado ni credenciales reales que puedan usarse en demostraciones (el Modo Sombra usa la Sandbox de IB cuando el cliente las provea); no hay testimonios, clientes ni case studies (es un sistema interno).

## Product Principles

1. **Seguridad por encima de comodidad:** nada sale del hardware local; toda acción crítica queda auditada de forma síncrona con IP interna.
2. **La decisión humana manda:** el sistema asiste por defecto y solo automatiza con autorización explícita y verificable (Gerente + OTP).
3. **Pérdida acotada, ganancia apalancada:** el riesgo diario tiene techo; cualquier exceso (gap de mercado) se registra, se explica y se justifica al usuario.
4. **Claridad bajo presión:** en volatilidad alta, estado, confianza y consecuencias deben entenderse en segundos y a máximo 2 toques.
5. **Todo es trazable:** cada predicción y operación es verificable retroactivamente (XAI, `audit_logs`, retención de 5 años).

## Accessibility & Inclusion

Requisito confirmado por el usuario (además de la usabilidad RNF de 2 toques):

- Los estados **nunca se comunican solo con color**: siempre texto + icono junto al color (verde/amarillo/rojo).
- Navegación completa por teclado con foco visible.
- Contraste AA como mínimo en texto y estados.
- Respetar `prefers-reduced-motion`: el parpadeo del estado rojo del Kill-Switch (SDD §6.3) debe ser lento y siempre acompañado de texto.
- Sin certificación WCAG formal comprometida.
