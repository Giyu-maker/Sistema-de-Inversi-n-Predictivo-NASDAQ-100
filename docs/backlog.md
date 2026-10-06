# Product Backlog y Planificación de Sprints
## Proyecto: Sistema de Inversión Predictivo NASDAQ-100 (RiTech-NASDAQ100)
## Cliente: RiTech SAS
## Despliegue: 100% On-Premise Local (Clúster de Intranet On-Premise de 3 Máquinas Personales)

---

### Descripción del Equipo y Roles
*   **Javier Martínez (Arquitecto Líder & DevOps Local):** Responsable de la planeación y diseño técnico (SDD), configuración de la red local del clúster de intranet on-premise de 3 máquinas corporativas, despliegue del backend y bases de datos Docker en la Máquina 2 (Central Node, 31GB RAM) en Colombia, y arquitectura del Kill-switch maestro.
*   **Javier Martínez / Dylan Merchancano (Especialista en IA & Backend Dev):** Responsable de la elicitación de requisitos y especificación formal (SRS), integración y entrenamiento local de la red neuronal PyTorch utilizando NVIDIA CUDA v12+ en la Máquina 1 (PRIME B450 AI Node con GPU GTX 1660 SUPER), validación de la explicabilidad (XAI) y diseño del motor matemático asimétrico.
*   **Dylan Merchancano (Developer Frontend & API Integration):** Responsable del levantamiento de requisitos, entrevistas de negocio, desarrollo de la interfaz de usuario Dashboard (React, Vite, Tailwind, shadcn/ui) operando en la Máquina 3 (Windows 11 i9 Vivobook), la ingesta de datos en tiempo real y la conexión local TCP con Interactive Brokers Sandbox.

---

### Planificación de Sprints (1 al 4)

El desarrollo del sistema para RiTech SAS se distribuye de manera balanceada en **4 Sprints** de 2 semanas de duración cada uno. El primer Sprint se destina por completo a la **planeación, entrevistas y diseño formal de requisitos y arquitectura (SRS y SDD)**, garantizando que todo el equipo técnico y el cliente tengan absoluta claridad en las metas. A partir del **Sprint 2** se inicia la ejecución técnica incremental basada en el enfoque de "Skeletal System" o Walking Skeleton para mitigar riesgos tempranamente.

#### SPRINT 1: Planeación, Requisitos y Diseño de Arquitectura
*   **Objetivo:** Consolidar el entendimiento del negocio mediante entrevistas estructuradas al cliente, definir metas profundas, alinear expectativas de stakeholders y formalizar la arquitectura distribuida on-premise mediante la entrega del SRS v1.1 y el SDD v1.2.
*   **Ítem 1: Elicitación de Requisitos y Entrevistas al Cliente (Dylan Merchancano & Javier Martínez)**
    *   *Descripción:* Preparación y ejecución de las sesiones de entrevistas, formulación de preguntas de visión estratégica (ASRs) y consolidación de las minutas con RiTech SAS.
    *   *Estimación:* 4 días | *Valor:* 10/10 | *Requisito:* RF-01, RF-02, RF-03.
*   **Ítem 2: Redacción del Software Requirements Specification (SRS) (Javier Martínez / Dylan Merchancano)**
    *   *Descripción:* Elaboración del SRS formal bajo el estándar IEEE 830, definiendo con precisión los 7 Requerimientos Funcionales y 6 No Funcionales.
    *   *Estimación:* 5 días | *Valor:* 10/10 | *Requisito:* Cumplimiento de Procesos de Ingeniería.
*   **Ítem 3: Redacción del Software Design Description (SDD) (Javier Martínez)**
    *   *Descripción:* Elaboración del SDD formal bajo el estándar IEEE 1016, especificando el modelo relacional físico en PostgreSQL, series temporales en InfluxDB, diagramas UML de contexto, secuencia y estados, y clases detalladas del clúster local de intranet.
    *   *Estimación:* 5 días | *Valor:* 10/10 | *Requisito:* Cumplimiento de Procesos de Arquitectura.

#### SPRINT 2: Infraestructura y Captura Base (El "Walking Skeleton")
*   **Objetivo:** Configurar la red del clúster de intranet local entre las 3 máquinas de RiTech SAS, levantar las bases de datos y backend Docker en la Máquina 2, e iniciar la ingesta del NASDAQ-100 calculando la lógica asimétrica de forma virtual.
*   **Ítem 4: Configuración del Entorno Físico y Docker Compose (Javier Martínez)**
    *   *Descripción:* Configuración de red local en intranet para las 3 máquinas, despliegue de contenedores Docker para PostgreSQL v16 e InfluxDB v2.7 en la Máquina 2 (31 GB RAM), y encriptación AES-256 local de las credenciales de API.
    *   *Estimación:* 5 días | *Valor:* 10/10 | *Requisito:* RNF-04, RNF-02, RNF-06.
*   **Ítem 5: IngestionEngine para Velas de 5M (Dylan Merchancano)**
    *   *Descripción:* Integración con la API externa de Alpha Vantage y guardado cronológico e inmutable en InfluxDB local de la Máquina 2.
    *   *Estimación:* 7 días | *Valor:* 10/10 | *Requisito:* RF-01, RNF-01.
*   **Ítem 6: Algoritmo de Lógica Asimétrica Virtual (Javier Martínez / Dylan Merchancano)**
    *   *Descripción:* Implementación matemática en Python FastAPI del cálculo de ganancias al doble (2x) y pérdidas acotadas (1.25%) al finalizar cada vela de 5 minutos.
    *   *Estimación:* 3 días | *Valor:* 10/10 | *Requisito:* RF-02.

#### SPRINT 3: Cerebro Predictivo e Interfaz de Usuario
*   **Objetivo:** Desarrollar y entrenar localmente el cerebro analítico del software usando la GPU de la Máquina 1 (NVIDIA GeForce GTX 1660 SUPER con CUDA) y crear el panel visual web React en la Máquina 3.
*   **Ítem 7: Red Neuronal Predictiva CUDA Local (Javier Martínez / Dylan Merchancano)**
    *   *Descripción:* Desarrollo del modelo predictivo PyTorch local en la Máquina 1, entrenamiento diario automatizado al cierre de jornada bursátil y generación de justificaciones técnicas (XAI) e indicador de confianza.
    *   *Estimación:* 12... | *Valor:* 9/10 | *Requisito:* RF-03.
*   **Ítem 8: Dashboard de Inversión Web Intranet (Dylan Merchancano)**
    *   *Descripción:* Interfaz web local React con shadcn/ui y gráficos interactivos con Recharts en la Máquina 3, que se comunica con la API de la Máquina 2 para mostrar velas de 5 min, proyecciones e historial.
    *   *Estimación:* 8... | *Valor:* 8/10 | *Requisito:* RF-05.
*   **Deuda conocida del Sprint 2 — RF-204: Exclusión de Operación en Volatilidad Extrema**
    *   *Descripción:* Implementar en `AsymmetricCalculator` las ventanas de exclusión (primeros y últimos 15 minutos del horario regular de mercado). Quedó fuera del Ítem 6 del Sprint 2 por prioridad Media en el SRS y por no bloquear el Walking Skeleton; marcado en código como `TODO(RF-204)`. No implementar a medias: requiere decidir el estado de sesión completo.
    *   *Requisito:* RF-204.

#### SPRINT 4: Seguridad Operativa, Integración con Broker y Cierre
*   **Objetivo:** Implementar los mecanismos de protección de capital (Kill-Switch), auditoría inmutable, notificaciones locales e integración real en cuenta Sandbox con Interactive Brokers para habilitar el Modo Sombra.
*   **Ítem 9: Módulo de Gestión de Riesgos y Kill-Switch Maestro (Javier Martínez)**
    *   *Descripción:* Desarrollo de la lógica de contención que corta asíncronamente el trading autónomo de la IA y envía peticiones síncronas bloqueantes al broker para cerrar posiciones.
    *   *Estimación:* 5 días | *Valor:* 9/10 | *Requisito:* RF-06, RNF-06.
*   **Ítem 10: Logs de Auditoría Inmutables a 5 Años (Javier Martínez)**
    *   *Descripción:* Persistencia inalterable de cada transacción, logs de acceso IP corporativos y explicaciones XAI en PostgreSQL de la Máquina 2 para fines regulatorios.
    *   *Estimación:* 6... | *Valor:* 8/10 | *Requisito:* RNF-05.
*   **Ítem 11: Módulo de Notificaciones Locales SMS/SMTP (Dylan Merchancano)**
    *   *Descripción:* Despacho asíncrono de alertas de pérdida de conexión de red o activación del Kill-switch directo a celulares de traders vía puerto serie GSM o servidor SMTP interno.
    *   *Estimación:* 4 días | *Valor:* 7/10 | *Requisito:* RF-04 (Asesor).
*   **Ítem 12: Integración de Interactive Brokers local vía Sockets TCP (Dylan Merchancano)**
    *   *Descripción:* Configuración del gateway de Interactive Brokers (TWS API) en el servidor local para ejecución automática asíncrona de órdenes (Modo Sombra y Autónomo).
    *   *Estimación:* 10 días | *Valor:* 6/10 | *Requisito:* RF-04 (Autónomo).
*   **Ítem 13: Generador de Reportes Financieros Tabulares (Dylan Merchancano)**
    *   *Descripción:* Módulo frontend en React para exportación asíncrona local de PDF/CSV con los rendimientos de RiTech frente al NASDAQ.
    *   *Estimación:* 4 días | *Valor:* 5/10 | *Requisito:* RF-07.
