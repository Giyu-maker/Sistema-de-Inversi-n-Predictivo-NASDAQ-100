# Software Requirements Specification (SRS)
## Proyecto: Sistema de Inversión Predictivo NASDAQ-100 (RiTech-NASDAQ100)
## Cliente: RiTech SAS
## Versión: 1.0 aprobado
## Fecha: 28 de Agosto de 2026
## Organización: Naranja's Team

---

### Historial de Revisiones
| Nombre | Fecha | Motivo del Cambio | Versión |
| :--- | :--- | :--- | :--- |
| Naranja's Team | 28/08/2026 | Creación inicial del SRS | 1.0 |

---

## 1. Introducción

### 1.1 Propósito
Este documento de Especificación de Requisitos de Software (SRS) define en su totalidad los requisitos funcionales, no funcionales y de restricción para el desarrollo del **Sistema de Inversión Predictivo NASDAQ-100** encargado por la empresa RiTech SAS. El propósito de este sistema es automatizar el monitoreo del índice NASDAQ-100, predecir su comportamiento mediante inteligencia artificial entrenada localmente y facilitar o ejecutar decisiones de inversión con base en un algoritmo de lógica asimétrica de ganancias y pérdidas.

Este documento está dirigido al equipo de ingenieros de software (Javier Martínez, Dylan Merchancano), a la junta directiva de RiTech SAS para actuar como el contrato técnico del sistema y guiar las pruebas de aceptación.

### 1.2 Convenciones del Documento
Este documento sigue las directrices del estándar IEEE 830 para especificaciones de requisitos de software.
*   **Requisitos Funcionales (RF-XXX):** Identifican capacidades de comportamiento que el sistema debe ejecutar de forma obligatoria.
*   **Requisitos No Funcionales (RNF-XXX):** Describen atributos de calidad que condicionan la operación del sistema (ISO/IEC 25010).
*   **Requisitos de Restricción (REST-XXX):** Establecen las limitaciones tecnológicas, geográficas o de negocio innegociables.
*   **Prioridad:** Alta (crítica para el MVP), Media (necesaria para el lanzamiento formal), Baja (deseable para versiones evolutivas).
*   **Tipografía:** Los nombres de las clases de datos y variables de bases de datos se representan en formato `monoespaciado`.

### 1.3 Alcance del Proyecto
El sistema **RiTech-NASDAQ100** es una solución de trading algorítmico e inteligencia artificial de uso exclusivo para RiTech SAS. Su alcance se define bajo las siguientes fronteras:
*   **Inclusión:** Ingesta de datos del índice NASDAQ-100 (incluyendo pre-market y after-hours); almacenamiento de series temporales; motor matemático de lógica asimétrica (ganancia 2x, pérdida máxima de 1.25x antes de la contención); entrenamiento local diario de un modelo predictivo; interfaz gráfica local (Dashboard Web de Intranet); gestión de riesgos (Kill-switch maestro); entorno de pruebas en Modo Sombra (Shadow Mode); y registro persistente de auditoría de transacciones por 5 años.
*   **Exclusión:** Se excluye de forma estricta cualquier infraestructura en la nube (AWS, Azure, GCP). El despliegue de base de datos, backend y frontend se distribuirá en un clúster de intranet local on-premise compuesto por tres computadores personales provistos por la empresa en Colombia, comunicados en red local y prescindiendo de cualquier infraestructura cloud.

### 1.4 Referencias
*   *ISO/IEC 25010: Systems and software engineering — Systems and software Quality Requirements and Evaluation (SQuaRE) — System and software quality models.*
*   *IEEE Std 830-1998 — IEEE Recommended Practice for Software Requirements Specifications.*
*   *Documento de Visión Estratégica RiTech SAS.*
*   *Minutas de la primera y segunda entrevista con el cliente RiTech SAS (Agosto 2026).*

---

## 2. Descripción General

### 2.1 Perspectiva del Producto
El sistema es un software independiente (stand-alone) que operará dentro de la red privada local (intranet) de RiTech SAS. Reemplazará directamente el proceso manual actual en el cual una secretaria monitorea visualmente las variaciones de precio del índice NASDAQ-100. Toda la persistencia de datos se realizará localmente mediante motores de base de datos instalados directamente en el hardware de la empresa, y la ejecución de órdenes interactuará con la API local de Interactive Brokers Gateway (TWS) instalada en la misma máquina física.

```
+------------------------------------------------------------------------------------------------------+
| CLÚSTER DE INTRANET ON-PREMISE (RiTech SAS)                                                           |
|                                                                                                      |
|  +---------------------------------+                                                                 |
|  | MÁQUINA 3 (Vivobook Laptop)     |                                                                 |
|  | - UI Cliente (React / Nginx)    |                                                                 |
|  | - Terminal de Operador/Gerente  |                                                                 |
|  +----------------+----------------+                                                                 |
|                   |                                                                                  |
|                   | HTTP (Intranet Local)                                                            |
|                   v                                                                                  |
|  +----------------+----------------+  API local TCP  +--------------------------------------------+ |
|  | MÁQUINA 2 (8CAD Central Node)   | <=============> | MÁQUINA 1 (PRIME B450 AI Node)             | |
|  | - Python FastAPI Backend Core   |                 | - PyTorch CUDA Engine (IA)                 | |
|  | - PostgreSQL (Transactional)    |                 | - GPU NVIDIA GTX 1660 SUPER [6GB VRAM]     | |
|  | - InfluxDB (Velas NASDAQ 5M)    |                 |   (Modelos Predictivos Ligeros)            | |
|  | - 31 GB RAM & 12 Cores          |                 | - 15.52 GB RAM & Ryzen 5 3600              | |
|  +----------------+----------------+                 +--------------------------------------------+ |
|                   |                                                                                  |
+-------------------|----------------------------------------------------------------------------------+
                    v
  +-----------------------------------+
  | APIs Externas (Conexión Internet) |
  | - Alpha Vantage API (Datos 5M)    |
  | - Interactive Brokers API Gateway |
  +-----------------------------------+
```

### 2.2 Clases de Usuarios y Características
*   **Administrador de TI (Dylan Merchancano):** Usuario técnico responsable de la configuración de red local, backups, mantenimiento del servidor y despliegue del software. Tiene acceso total al sistema.
*   **Especialista en IA / Analista de Datos (Javier Martínez):** Responsable del monitoreo de métricas del modelo, calibración de hiperparámetros, reentrenamiento diario de la IA y análisis de falsos positivos/negativos.
*   **Operador de Inversiones (Secretaria/Traders):** Encargado de utilizar el Modo Asesor, recibir alertas, y ejecutar órdenes manuales. Tienen el privilegio de activar el Kill-switch maestro ante emergencias.
*   **Gerente General/Sponsor (Aida Liliana Rosero Ruiz):** Único usuario con autorización formal para habilitar el paso de Modo Asesor a Modo Autónomo. Define los perfiles de riesgo y autoriza auditorías.

### 2.3 Entorno Operativo
El software se instalará en una arquitectura distribuida de red local (intranet on-premise) utilizando de manera eficiente las tres máquinas personales, adaptando las responsabilidades de software a las capacidades físicas específicas de cada equipo:

1.  **MÁQUINA 1: Nodo de Cómputo e Inteligencia Artificial (Javier Martínez)**
    *   **Especificaciones Técnicas:** Tarjeta madre ASUS PRIME B450M-A II, Procesador AMD Ryzen 5 3600 (6 núcleos físicos, 12 hilos de ejecución a 4.21 GHz), 15.52 GB de memoria RAM, disco de 1 TB con sistema de archivos Btrfs, GPU dedicada **NVIDIA GeForce GTX 1660 SUPER (Discrete)**. Sistema Operativo CachyOS x86_64 (Linux Kernel 7.1.2-3-cachyos optimizado).
    *   **Rol en el Clúster:** Encargado del alojamiento, entrenamiento asíncrono diario y ejecución en tiempo real del modelo predictivo ligero basado en PyTorch. Es la única máquina con hardware dedicado NVIDIA CUDA (v12+), por lo que actúa como el servidor de inferencia del clúster local.
2.  **MÁQUINA 2: Nodo Central de Servicios y Base de Datos (Javier Martínez)**
    *   **Especificaciones Técnicas:** Board de servidor 8CAD, Procesador Intel Core 5 120U (12 núcleos, 16 hilos de ejecución a 5.00 GHz), **30.99 GB de memoria RAM**, disco de 500 GB con sistema de archivos Btrfs. GPU integrada Intel Graphics. Sistema Operativo CachyOS x86_64 (Linux Kernel 7.2.0-1-cachyos).
    *   **Rol en el Clúster:** Nodo maestro del backend y bases de datos. Aprovechando sus generosos 31 GB de memoria RAM y sus 12 núcleos de CPU, hosteará los contenedores Docker para **PostgreSQL v16** (datos relacionales y logs de auditoría de 5 años), **InfluxDB v2.7** (series temporales de velas de 5 minutos del NASDAQ-100) y la API del backend **FastAPI** en Python 3.12, además del Interactive Brokers Gateway.
3.  **MÁQUINA 3: Estación de Trabajo del Operador y Servidor Frontend (Dylan Merchancano)**
    *   **Especificaciones Técnicas:** Portátil ASUSTeK Vivobook X1605VA, Procesador 13th Gen Intel Core i9-13900H (14 núcleos, 20 hilos @ 2.60 GHz), 23.64 GB de memoria RAM, disco SSD de 1 TB con sistema de archivos NTFS. GPU integrada Intel Iris Xe Graphics. Sistema Operativo Microsoft Windows 11 Pro de 64 bits (Build 26200).
    *   **Rol en el Clúster:** Terminal física del operador y servidor web estático. Se utiliza como el computador de acceso principal por la Secretaria (Operador) y el Gerente para interactuar con la interfaz del Dashboard web en React + Vite servido en red local, ejecutando pruebas manuales y gestionando el Kill-switch maestro en la intranet.

### 2.4 Restricciones de Diseño e Implementación
*   **REST-01 (Soberanía y No Cloud):** El sistema debe instalarse 100% de manera local. Queda expresamente prohibido contratar o usar servicios de nube como AWS, Azure o GCP.
*   **REST-02 (Confidencialidad de la IA):** No se permite enviar datos financieros reales a APIs de Inteligencia Artificial externas (como OpenAI, Anthropic u otras de terceros). Todo el procesamiento y entrenamiento del modelo predictivo debe realizarse in-house utilizando los recursos locales del servidor.
*   **REST-03 (Presupuesto Máximo):** El presupuesto total aprobado para el desarrollo e infraestructura física es de \$20,000,000 COP.
*   **REST-04 (Fecha Límite):** La fecha límite improrrogable para la puesta en producción es a principios de noviembre de 2026.
*   **REST-05 (Cumplimiento de Residencia):** Toda la información transaccional y los datos de clientes deben mantenerse físicamente dentro del territorio de Colombia por razones legales de soberanía de datos.

### 2.5 Suposiciones y Dependencias
*   Se asume que la red interna de RiTech SAS cuenta con suficiente ancho de banda y estabilidad para mantener una conexión externa constante hacia Interactive Brokers Gateway y la API de datos Alpha Vantage.
*   Se asume que el cliente proporcionará las credenciales y el acceso a una cuenta Sandbox (entorno de pruebas) de Interactive Brokers para la correcta verificación del Modo Sombra en el Sprint 4.

---

## 3. Funcionalidades del Sistema

### 3.1 Ingesta y Monitoreo del NASDAQ-100 (Feature 1)
#### 3.1.1 Descripción
El sistema debe realizar una ingesta constante de datos de precios (velas o candlesticks de 5 minutos) del índice NASDAQ-100 desde el proveedor externo de datos. Esta ingesta debe cubrir el pre-market (desde las 4:00 AM EST), el mercado abierto y el after-hours (hasta las 8:00 PM EST).
#### 3.1.2 Secuencia de Estímulo/Respuesta
1.  **Estímulo:** El programador de tareas (Cron) del sistema dispara una petición de red local a la API de Alpha Vantage cada 5 minutos.
2.  **Respuesta:** La API devuelve el precio de apertura, cierre, máximo, mínimo y volumen de la vela de 5 minutos.
3.  **Procesamiento:** El backend procesa el payload, valida que no haya anomalías (por ejemplo, valores negativos) y persiste los datos directamente en la base de datos de series temporales InfluxDB local.

#### 3.1.3 Requisitos Funcionales
*   **RF-101 (Frecuencia de Muestreo):** El sistema debe capturar el precio del índice utilizando intervalos síncronos de exactamente 5 minutos (velas de 5M), descartando el monitoreo agresivo a nivel de milisegundos para evitar costos excesivos de comisiones y latencias inviables. (Prioridad: Alta)
*   **RF-102 (Monitoreo de Horarios Extendidos):** El módulo de ingesta debe monitorear y almacenar los datos correspondientes a pre-market, horario regular de Nueva York y after-hours. (Prioridad: Alta)
*   **RF-103 (Almacenamiento Local de Series):** Todos los puntos de datos ingeridos deben ser grabados en InfluxDB local con un índice basado en marcas de tiempo (Epoch ms). (Prioridad: Alta)

### 3.2 Motor de Lógica Asimétrica (Feature 2)
#### 3.2.1 Descripción
Este motor es el núcleo matemático que evalúa las ganancias o pérdidas de RiTech SAS. Aplica un factor de apalancamiento asimétrico: gana al doble (2x) el comportamiento positivo del índice, pero limita las pérdidas a un 1.25% en condiciones normales de mercado.
#### 3.2.2 Secuencia de Estímulo/Respuesta
1.  **Estímulo:** Se cierra una vela de 5 minutos y se actualiza el precio actual.
2.  **Procesamiento:** El sistema calcula la diferencia porcentual con respecto al precio base de la sesión de inversión activa.
3.  **Respuesta:** Si el índice NASDAQ sube un 2%, el sistema calcula y proyecta un retorno del 4% en el rendimiento de RiTech. Si el índice NASDAQ baja un 2%, el sistema calcula la pérdida y, si esta traspasa el umbral del 1.25%, genera inmediatamente una directiva de contingencia.

#### 3.2.3 Requisitos Funcionales
*   **RF-201 (Cálculo de Ganancias Apalancadas):** El sistema debe registrar las ganancias virtuales o reales de RiTech SAS multiplicando por un factor de 2.0 (2x) el porcentaje positivo del índice NASDAQ-100 durante la vigencia de la inversión. (Prioridad: Alta)
*   **RF-202 (Límite de Pérdida Teórica):** El algoritmo debe establecer un límite máximo de pérdida nominal del 1.25% sobre la inversión de la sesión. (Prioridad: Alta)
*   **RF-203 (Manejo de Gaps de Mercado):** Ante un gap de mercado (salto violento de precio donde el índice baja de golpe un 2% o más sin operaciones intermedias), el motor de lógica asimétrica debe registrar la pérdida real final aunque esta supere el 1.25% teórico, informando al usuario la diferencia y justificando la operación. (Prioridad: Alta)
*   **RF-204 (Exclusión de Operación en Volatilidad Extrema):** El motor debe abstenerse de abrir nuevas posiciones o tomar decisiones de inversión durante los primeros y últimos 15 minutos del horario regular del mercado debido a la alta volatilidad e inestabilidad del índice NASDAQ. (Prioridad: Media)

### 3.3 Inteligencia Artificial Predictiva Local (Feature 3)
#### 3.3.1 Descripción
El sistema utiliza un módulo de IA entrenado y ejecutado localmente para predecir las tendencias alcistas o bajistas del índice NASDAQ-100 para los próximos periodos.
#### 3.3.2 Secuencia de Estímulo/Respuesta
1.  **Estímulo:** Finaliza la sesión del mercado del día anterior (después de las 8:00 PM EST).
2.  **Procesamiento:** El servidor inicia el proceso automatizado de reentrenamiento del modelo utilizando los históricos consolidados en las bases de datos locales.
3.  **Respuesta:** Se genera y publica la nueva versión del modelo predictivo local, arrojando el porcentaje de tasa de acierto y el ratio de confianza.

#### 3.3.3 Requisitos Funcionales
*   **RF-301 (Entrenamiento Local Estricto):** El entrenamiento del modelo predictivo debe ejecutarse única y exclusivamente de forma local utilizando los recursos de la GPU local. Queda prohibida la transmisión de datos fuera del servidor. (Prioridad: Alta)
*   **RF-302 (Frecuencia de Reentrenamiento):** El modelo de inteligencia artificial debe reentrenarse y calibrarse diariamente de forma automatizada al cierre de cada jornada bursátil. (Prioridad: Alta)
*   **RF-303 (Explicabilidad Técnica - XAI):** Cada decisión o sugerencia de compra/venta generada por la IA debe acompañarse de un registro detallado en lenguaje técnico financiero (por ejemplo, basados en volumen de compra/venta y tendencias de velas de 5 min) que justifique la decisión para auditorías de negocios. (Prioridad: Alta)
*   **RF-304 (Cálculo del Ratio de Confianza):** La IA debe adjuntar un indicador de probabilidad o "ratio de confianza" (0% a 100%) a cada predicción emitida. Si el ratio de confianza es menor al 75%, el sistema no abrirá operaciones automáticas en Modo Autónomo. (Prioridad: Media)

### 3.4 Gestión de Operaciones Duales (Feature 4)
#### 3.4.1 Descripción
Permite configurar el modo de ejecución del software para operar como un asistente interactivo o como un operador de inversión autónomo.
#### 3.4.2 Secuencia de Estímulo/Respuesta
1.  **Estímulo:** El Gerente General accede al Dashboard e interactúa con el selector de ejecución.
2.  **Respuesta:** El sistema cambia su estado operativo, requiriendo autenticación multifactor local.

#### 3.4.3 Requisitos Funcionales
*   **RF-401 (Modo Asesor):** El sistema debe funcionar en primera instancia emitiendo recomendaciones y alertas de inversión claras en la interfaz del usuario para que un operador humano (la secretaria o el trader) realice la acción de forma manual. (Prioridad: Alta)
*   **RF-402 (Modo Autónomo):** Al activarse este modo por autorización de la gerencia, el sistema tiene la potestad de ejecutar compras y ventas automáticas enviando comandos de trading síncronos al broker directamente y sin intervención humana. (Prioridad: Alta)
*   **RF-403 (Gestión de Perfiles de Riesgo):** El sistema debe permitir configurar tres perfiles de riesgo independientes aplicables al Modo Autónomo: Conservador, Moderado y Agresivo, alterando los umbrales de apertura de operaciones. (Prioridad: Media)

### 3.5 Módulo de Gestión de Riesgos y Kill-Switch (Feature 5)
#### 3.5.1 Descripción
Funcionalidad de seguridad crítica para la contención rápida de pérdidas en caso de fallos del algoritmo bursátil o desplomes repentinos del mercado.
#### 3.5.2 Secuencia de Estímulo/Respuesta
1.  **Estímulo:** El operador pulsa el botón rojo de "Kill-Switch Maestro" en el Dashboard, o se dispara de manera automatizada al cruzar el umbral del 1.25% de pérdida en el día.
2.  **Procesamiento:** El backend bloquea inmediatamente la lógica de inversión activa del sistema.
3.  **Respuesta:** Se envían peticiones síncronas bloqueantes a la API de Interactive Brokers para liquidar (vender) inmediatamente todas las posiciones abiertas al precio actual de mercado.

#### 3.5.3 Requisitos Funcionales
*   **RF-501 (Interruptor Maestro Unico):** El sistema debe proveer un único botón de emergencia de fácil acceso en el Dashboard que actúe como Kill-switch global. (Prioridad: Alta)
*   **RF-502 (Liquidación Síncrona de Posiciones):** Al dispararse el Kill-switch, el sistema debe liquidar (cerrar) de manera inmediata todas las posiciones abiertas al precio disponible del mercado, en lugar de solo congelar la apertura de nuevas posiciones. (Prioridad: Alta)
*   **RF-503 (Bloqueo de Reapertura):** Al estar activo el Kill-switch, el software debe rechazar cualquier nueva orden automática o sugerencia de compra hasta que un operador con rol de Administrador de TI o Gerente restablezca la configuración de manera explícita. (Prioridad: Alta)

### 3.6 Modo Sombra (Shadow Mode) (Feature 6)
#### 3.6.1 Descripción
Entorno simulado que permite evaluar el rendimiento del sistema bursátil sin poner en riesgo el capital de RiTech SAS.
#### 3.6.2 Secuencia de Estímulo/Respuesta
1.  **Estímulo:** Se activa la simulación en Modo Sombra desde la configuración local.
2.  **Procesamiento:** La IA genera decisiones y envía órdenes ficticias con datos en tiempo real hacia una cuenta Sandbox del broker.
3.  **Respuesta:** El sistema registra las órdenes simuladas, calcula la rentabilidad asimétrica teórica y permite la trazabilidad completa del comportamiento antes del Go-Live.

#### 3.6.3 Requisitos Funcionales
*   **RF-601 (Simulación con Datos Reales):** El sistema debe poseer un Modo Sombra para operar utilizando los datos reales en tiempo real del NASDAQ-100 para simular órdenes sin comprometer capital. (Prioridad: Alta)
*   **RF-602 (Trazabilidad de Historial de Simulación):** Las órdenes simuladas deben guardarse por separado en PostgreSQL con la marca distintiva `is_simulated = true` para evitar cruzamientos con auditorías reales. (Prioridad: Alta)

### 3.7 Registro de Auditoría y Reportes (Feature 7)
#### 3.7.1 Descripción
Registro persistente e inalterable de la actividad para fines tributarios, legales y regulatorios.
#### 3.7.2 Requisitos Funcionales
*   **RF-701 (Conservación de Datos por 5 Años):** Los logs de transacciones, las decisiones de inversión de la IA, sus explicaciones técnicas y los estados financieros diarios deben conservarse de manera persistente en la base de datos local de PostgreSQL por un período mínimo de **5 años**. (Prioridad: Alta)
*   **RF-702 (Reportes en PDF y CSV):** El sistema debe generar localmente reportes financieros que detallen el rendimiento asimétrico acumulado, la tasa de efectividad de la IA y el histórico de posiciones cerradas, permitiendo la descarga en formatos PDF y CSV sin necesidad de conectarse a ningún servidor cloud. (Prioridad: Alta)

---

## 4. Requisitos de Datos

### 4.1 Modelo Lógico de Datos
El diseño almacena las relaciones locales entre la configuración, los datos temporales del NASDAQ, las predicciones hechas por el modelo local de IA y la ejecución final de transacciones.

```
 +------------------+           +------------------+           +------------------+
 |  Configuration   |           |  NasdaqTick (TS) |           |   AIPrediction   |
 +------------------+           +------------------+           +------------------+
 | id: UUID [PK]    |           | time: Timestamp  |           | id: UUID [PK]    |
 | max_loss_day: F  |           | price_open: Float|           | timestamp: BigInt|
 | is_autonomous: B |           | price_close: F   |           | prediction: Enum |
 | active_profile: E|           | volume: Float    |           | confidence: Float|
 +------------------+           +------------------+           | explanation: Text|
                                                               +--------+---------+
                                                                        |
                                                                        | genera
                                                                        v
 +------------------+           +------------------+           +--------+---------+
 |   AuditLog       |           |   TraderAccount  |           |   Transaction    |
 +------------------+           +------------------+           +------------------+
 | id: UUID [PK]    |           | id: UUID [PK]    |           | id: UUID [PK]    |
 | timestamp: BigInt|           | balance: Float   |           | prediction_id: FK|
 | user_id: UUID [FK]|          | risk_profile: E  | <-------+ | broker_order_id:S|
 | action: String   |           +--------+---------+           | type: Enum (C/V) |
 | payload: Text    |                    |                     | quantity: Float  |
 +------------------+                    | posee               | price_exec: Float|
                                         v                     | timestamp: BigInt|
                                +--------+---------+           | is_simulated: B  |
                                | TraderUser       |           +------------------+
                                +------------------+
                                | id: UUID [PK]    |
                                | username: String |
                                | role: Enum       |
                                +------------------+
```

### 4.2 Diccionario de Datos

#### 4.2.1 Entidad `Configuration` (PostgreSQL)
*   `id`: UUID. Clave primaria única generada localmente.
*   `max_loss_day`: Float. Límite máximo de pérdida diaria tolerada (por defecto 1.25%).
*   `is_autonomous`: Boolean. Indica si la ejecución está en Modo Asesor (`false`) o Modo Autónomo (`true`).
*   `active_profile`: Enum (`CONVENTIONAL` | `MODERATE` | `AGGRESSIVE`). Perfil de riesgo de RiTech SAS para la sesión activa.

#### 4.2.2 Entidad `NasdaqTick` (InfluxDB - Time Series)
*   `time`: Timestamp. Marca de tiempo única a nivel de nanosegundos (Epoch ms).
*   `price_open`: Float. Precio de apertura de la vela de 5 minutos.
*   `price_close`: Float. Precio de cierre de la vela de 5 minutos.
*   `volume`: Float. Volumen bursátil operado en la vela de 5 minutos.

#### 4.2.3 Entidad `AIPrediction` (PostgreSQL)
*   `id`: UUID. Clave primaria.
*   `timestamp`: BigInt. Fecha y hora en la que se generó la predicción (Epoch ms).
*   `prediction`: Enum (`UP` | `DOWN` | `STABLE`). Proyección de tendencia calculada por el modelo.
*   `confidence`: Float. Porcentaje de confiabilidad arrojado por el modelo predictivo local (0.00 a 1.00).
*   `explanation`: Text. Justificación técnica de la predicción en base a datos históricos.

#### 4.2.4 Entidad `Transaction` (PostgreSQL)
*   `id`: UUID. Clave primaria.
*   `prediction_id`: UUID. Clave foránea que apunta a `AIPrediction.id`.
*   `broker_order_id`: String. Identificador de orden retornado por la API de Interactive Brokers.
*   `type`: Enum (`BUY` | `SELL`). Tipo de transacción ejecutada.
*   `quantity`: Float. Cantidad de contratos o activos negociados.
*   `price_exec`: Float. Precio real de ejecución retornado por el broker.
*   `timestamp`: BigInt. Epoch ms de la ejecución.
*   `is_simulated`: Boolean. Indica si fue en Modo Sombra (`true`) o cuenta real (`false`).

### 4.3 Reportes Financieros
El sistema generará dos reportes consolidados directamente en el servidor local:
1.  **Reporte de Trazabilidad Asimétrica:** Documento tabular con columnas: Fecha, Inversión Base (USD), Variación NASDAQ (%), Rendimiento RiTech (%), Pérdida/Ganancia (COP) y Estado de la operación.
2.  **Auditoría de Decisiones de IA:** Listado para auditores externos que une cada transacción real con el `prediction_id` correspondiente, mostrando el porcentaje de confianza y la justificación textual del modelo (XAI).

### 4.4 Adquisición, Integridad, Retención y Eliminación de Datos
*   **Adquisición:** Los datos se toman de la API externa Alpha Vantage de manera programada y de las operaciones de Interactive Brokers local.
*   **Integridad:** PostgreSQL gestiona la integridad referencial. InfluxDB almacena datos inmutables donde no se permiten escrituras destructivas o modificaciones de registros de precios históricos.
*   **Retención:** Los datos financieros, transaccionales y de logs de auditoría se retienen estrictamente por **5 años** en disco local.
*   **Eliminación:** El sistema proveerá una herramienta de purga accesible únicamente para el Administrador de TI para limpiar datos transaccionales de simulación obsoletos que superen los 5 años, guardando un registro de la purga en el archivo de logs físicos del servidor central.

---

## 5. Requisitos de Interfaces Externas

### 5.1 Interfaces de Usuario
La interfaz del usuario se desplegará localmente en la intranet corporativa:
*   La aplicación web se construirá con **React, Vite, Tailwind CSS y componentes de shadcn/ui**.
*   El diseño debe ser completamente responsivo para funcionar en pantallas de escritorio, portátiles y dispositivos móviles mediante adaptabilidad CSS.
*   **Modo Oscuro / Claro:** La aplicación contará con un toggle rápido y estéticamente pulido utilizando variables CSS HSL con persistencia en la base de datos local para no forzar la vista del analista en horas de mercado extendidas.

### 5.2 Interfaces de Software
*   **API de Mercado (NASDAQ-100):** El backend se conectará localmente mediante peticiones HTTP asíncronas con la API externa Alpha Vantage para obtener el precio del índice.
*   **API del Broker:** La conexión se realizará utilizando el protocolo TCP/IP local de **Interactive Brokers Gateway (TWS API)** instalado en la misma máquina física, eliminando dependencias de red cloud.
*   **Base de Datos local:** Utilización de las librerías oficiales de Python para interactuar de forma síncrona con PostgreSQL (`psycopg2`) y asíncrona con InfluxDB (`influxdb-client`).

### 5.3 Interfaces de Hardware
*   **GPU Local:** El módulo de IA debe integrarse con las librerías aceleradoras de hardware **NVIDIA CUDA v12+** instaladas localmente en el servidor para el entrenamiento diario de los modelos de Deep Learning (ej. PyTorch).

### 5.4 Interfaces de Comunicaciones
*   **Alertas SMS:** Para el envío de la notificación del Ítem 7 del backlog, se utilizará una pasarela local de módem celular GSM conectado al servidor mediante puerto serie USB, o un servicio API SMTP/SMS local para el despacho de alertas de contingencia a los celulares de los traders.
*   **Alertas de Correo:** Configuración de un servidor local SMTP para envío directo de correos sin pasar por nubes externas de correo transaccional.

---

## 6. Atributos de Calidad (ISO/IEC 25010)

### 6.1 Usabilidad
*   **RNF-001 (Operabilidad):** La interfaz gráfica debe priorizar el flujo ágil de decisiones. Las acciones críticas como "Iniciar Sesión", "Activar Modo Autónomo" o "Ejecutar Kill-Switch" deben estar a un máximo de 2 toques desde el Dashboard.
*   **RNF-002 (Validación de Formularios):** Todos los formularios del sistema (ej. cambio de parámetros de pérdida máxima) deben realizar validaciones locales del lado del cliente utilizando Zod y React Hook Form antes de enviarse al backend local, mostrando mensajes de error inline claros en español neutro.

### 6.2 Eficiencia de Desempeño
*   **RNF-003 (Tiempo de Cómputo Predictivo):** El tiempo necesario para procesar una nueva vela de 5 minutos, consultar InfluxDB, ejecutar el modelo de IA local, calcular la asimetría y actualizar la interfaz gráfica no debe superar los **500 ms** en el servidor local.
*   **RNF-004 (Velocidad de Carga Inicial):** La interfaz web cargada a través de la intranet local de RiTech SAS debe inicializarse completamente en el navegador en menos de **1.5 segundos**.

### 6.3 Seguridad
*   **RNF-005 (Aislamiento de Datos):** Ningún dato de transacciones reales, contraseñas, saldos o estrategias financieras debe salir del hardware local de RiTech SAS, garantizando protección absoluta frente a accesos no autorizados externos o fugas en la nube.
*   **RNF-006 (Encriptación Local):** Los archivos de configuración que contengan claves de APIs externas (Interactive Brokers y Alpha Vantage) deben estar encriptados en reposo en el disco local utilizando un algoritmo de encriptación fuerte (AES-256).

### 6.4 Disponibilidad
*   **RNF-007 (SLA Bursátil):** El sistema debe garantizar una disponibilidad del **99.9%** durante todo el horario regular y extendido de cotización del índice NASDAQ-100 (4:00 AM a 8:00 PM EST, de lunes a viernes).
*   **RNF-008 (Ventanas de Mantenimiento):** Todas las tareas de mantenimiento, actualizaciones de código o backups masivos se realizarán de manera programada exclusivamente después de las 6:00 PM (hora Colombia) cuando el volumen del after-hours es insignificante.

### 6.5 Fiabilidad
*   **RNF-009 (Tolerancia a Caídas de Red):** En caso de pérdida de conexión a internet hacia la API del mercado, el sistema debe registrar el incidente, suspender inmediatamente la colocación de nuevas órdenes en Interactive Brokers para evitar riesgos de desfases de precios, y alertar síncronamente al operador. Al restaurarse la red, la reanudación debe realizarse de forma segura previniendo la duplicación de órdenes transaccionales.

### 6.6 Seguridad Funcional (Safety)
*   **RNF-010 (Ejecución del Botón de Pánico):** El Kill-switch maestro debe contar con prioridad de ejecución a nivel de hilos de CPU. Al activarse, debe interrumpir síncronamente cualquier otro cálculo matemático del backend y bloquear la colocación de nuevas órdenes antes de despachar la liquidación inmediata de posiciones abiertas en Interactive Brokers.

### 6.7 Mantenibilidad
*   **RNF-011 (Tipado Estricto):** Toda la base de código debe usar TypeScript estricto en el frontend y tipado estricto con Pydantic en el backend de Python. Queda prohibido el uso de tipos implícitos o la palabra clave `any` sin justificación exhaustiva aprobada por el líder de arquitectura.

---

## 7. Internacionalización y Localización

*   **Idioma de la UI:** El sistema se desplegará exclusivamente con la interfaz en español neutro para asegurar el entendimiento óptimo del operador.
*   **Formatos:** Las fechas se presentarán bajo la convención hispanohablante `DD/MM/YYYY HH:mm:ss`.
*   **Unidades y Moneda:** El sistema debe registrar las operaciones bursátiles en Dólares Estadounidenses (USD) dado que NASDAQ opera en dicho mercado, pero permitirá configurar la visualización del retorno de capital y reportes convertidos a Pesos Colombianos (COP) utilizando una tasa de cambio fija o actualizada manualmente.

---

## 8. Otros Requisitos

*   **REST-06 (Pruebas de Penetración):** La auditoría de seguridad y las pruebas de penetración para proteger el servidor local de intrusiones en la red corporativa son de entera responsabilidad técnica y económica del equipo de desarrollo de Naranja's Team durante la fase final de despliegue en Noviembre 2026.
*   **RF-801 (Capacitación y Documentación de Runbook):** El equipo de ingeniería debe entregar un manual de usuario detallado (Runbook) al operador y secretaria de RiTech que especifique la secuencia exacta de mitigación en caso de que ocurran fallos en los sistemas de comunicaciones de Interactive Brokers.

---

## 9. Glosario

*   **Interactive Brokers Gateway (TWS API):** Interfaz local que actúa como pasarela para enviar y recibir órdenes hacia el broker Interactive Brokers mediante conexiones TCP locales.
*   **Modo Sombra (Shadow Mode):** Configuración en la cual la IA genera transacciones virtuales basadas en el flujo en vivo del NASDAQ sin comprometer fondos monetarios de la empresa en Interactive Brokers.
*   **Kill-Switch:** Protocolo de seguridad que cierra todas las operaciones activas y bloquea el sistema ante pérdidas severas o fallos imprevistos.
*   **PaaS (Platform as a Service):** En un contexto local, hace referencia al empaquetamiento del software en microservicios locales orquestados por Docker Compose en el servidor físico.
*   **XAI (Explainable Artificial Intelligence):** Metodologías en inteligencia artificial que permiten entender y rastrear el razonamiento lógico que usó una máquina para tomar una decisión bursátil.

---

## 10. Modelos de Análisis

### 10.1 Arquitectura por Capas Local
*   **UI Presentacional (React + shadcn/ui):** Consume los endpoints expuestos localmente.
*   **FastAPI Backend (Python 3.12):** Lógica asimétrica, validaciones de seguridad e integraciones.
*   **Capa de Servicios y IA:** Lógica del modelo predictivo que lee de las bases de datos e interactúa con la GPU.
*   **Base de Datos local:** InfluxDB y PostgreSQL.

### 10.2 Flujo de Cómputo de Inversión
```
+------------------+     5 min     +--------------------+     Almacena     +-------------------+
| API Alpha Vantage| ============> | Ingestor Python 5M | ===============> | InfluxDB local    |
+------------------+               +---------+----------+                  +-------------------+
                                             |
                                             v Evalúa tendencia
                                   +---------+----------+
                                   | IA Predictiva CUDA |
                                   +---------+----------+
                                             | Genera predicción > 75%
                                             v
                                   +---------+----------+
                                   | Lógica Asimétrica  |
                                   +---------+----------+
                                             | Ejecuta
                                             v
                                   +---------+----------+
                                   | IB Gateway API     |
                                   +--------------------+
```
