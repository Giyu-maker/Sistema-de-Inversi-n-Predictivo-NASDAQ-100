# Software Design Description (SDD)
## Proyecto: Sistema de Inversión Predictivo NASDAQ-100 (RiTech-NASDAQ100)
## Cliente: RiTech SAS
## Versión: 1.1 aprobado
## Fecha: 17 de Octubre de 2026
## Organización: Naranja's Team

---

### Historial de Revisiones
| Nombre | Fecha | Motivo del Cambio | Versión |
| :--- | :--- | :--- | :--- |
| Naranja's Team | 30/08/2026 | Creación inicial del SDD | 1.0 |
| Naranja's Team | 17/10/2026 | CR-001: `price_high`/`price_low` en `write_to_influx()` (§5.1.2) y nueva §3.1.2 con la entidad `NasdaqTick`; CR-003: guarda de inmutabilidad en `write_to_influx()` y operaciones `existing_timestamps()`/`write_candles_batch()` (§5.1) | 1.1 |

---

## 1. Introducción

### 1.1 Propósito
Este documento de Descripción de Diseño de Software (SDD) detalla la arquitectura del sistema, la descomposición de módulos, los flujos de datos físicos, los diagramas lógicos y las especificaciones detalladas de las clases de código para el desarrollo del **Sistema de Inversión Predictivo NASDAQ-100** de RiTech SAS. Este documento traduce los requisitos del SRS v1.0 en planos técnicos precisos para guiar la construcción, integración y mantenimiento de la aplicación por parte de los ingenieros asignados (Javier Martínez, Dylan Merchancano).

### 1.2 Alcance
Este SDD detalla el software desde su diseño arquitectónico de alto nivel hasta el diseño detallado de los módulos de ingesta, cálculo, inteligencia artificial, seguridad y broker.
*   **Enfoque:** Arquitectura física on-premise local. No se utilizará infraestructura cloud pública (AWS, Azure o GCP).
*   **Servicio local:** El backend de Python FastAPI correrá en la Máquina 2 (Intel Core 5, 31GB RAM), la IA de PyTorch acelerada por CUDA correrá en la Máquina 1 (Ryzen 5, GPU GTX 1660 Super), el frontend en React se servirá desde la Máquina 3 (Intel i9 Windows 11) o mediante Nginx en la Máquina 2, mientras que PostgreSQL e InfluxDB se hostearán mediante contenedores en la Máquina 2, comunicándose en clúster local de intranet de RiTech SAS en Colombia.

### 1.3 Definiciones, acrónimos y abreviaturas
*   **SDD:** Software Design Description (Descripción de Diseño de Software).
*   **SRS:** Software Requirements Specification (Especificación de Requisitos de Software).
*   **TWS API:** Trader Workstation API. Interfaz TCP local de Interactive Brokers para recibir comandos de trading.
*   **CUDA:** Compute Unified Device Architecture. Interfaz de hardware de NVIDIA para el cómputo masivo y aceleración de algoritmos de IA en GPUs locales.
*   **Docker Compose:** Herramienta local para definir y ejecutar aplicaciones multi-contenedor Docker en una única máquina local de forma aislada.
*   **XAI:** Explainable Artificial Intelligence. Arquitectura de explicabilidad de modelos de IA.

### 1.4 Referencias
*   *IEEE Std 1016-2009 — IEEE Standard for Information Technology — Systems Design — Software Design Descriptions.*
*   *Software Architecture in Practice, Fourth Edition (Bass, Clements, Kazman).*
*   *Especificación de Requisitos de Software (SRS) RiTech-NASDAQ100 v1.0.*

---

## 2. Arquitectura del Sistema

### 2.1 Visión General
El sistema RiTech-NASDAQ100 se estructura como una **arquitectura local de tres capas (Three-tier Architecture) on-premise**, empaquetada mediante contenedores Docker locales orquestados por Docker Compose. Esto asegura un aislamiento total del entorno de desarrollo y producción sin necesidad de dependencias en nubes públicas, optimizando la desplegabilidad y mantenibilidad.

### 2.2 Descomposición en Capas (Arquitectura de Referencia)

```
+-------------------------------------------------------------------------------------------------------+
| CAPA DE PRESENTACIÓN - MÁQUINA 3 (Windows 11 Laptop i9 / 24 GB RAM)                                   |
|  - Dashboard Web React + Vite: Terminal física del operador y visualización en intranet local         |
+-------------------------------------------------------------------------------------------------------+
                                        || HTTPS (Peticiones REST por Red Intranet a Máquina 2)
                                        \/
+-------------------------------------------------------------------------------------------------------+
| CAPA DE SERVICIO & BACKEND - MÁQUINA 2 (Linux CachyOS Intel Core 5 / 31 GB RAM)                       |
|  - FastAPI Web API (Python 3.12) ejecutando el backend central, ruteador y cálculos asimétricos       |
|  - IngestionEngine: Captura y almacenamiento de velas de 5M del NASDAQ-100                            |
|  - RiskManager: Contención de pérdidas, perfiles de riesgo y administración del Kill-Switch           |
|  - BrokerGateway: Comunicación local vía sockets TCP hacia Interactive Brokers Sandbox               |
+-------------------------------------------------------------------------------------------------------+
                || REST API (Llamadas de Inferencia)                        || Conexiones TCP Docker Local
                \/                                                          \/
+-------------------------------------------------------+  +--------------------------------------------+
| CAPA DE INTELIGENCIA ARTIFICIAL - MÁQUINA 1           |  | CAPA DE DATOS Y PERSISTENCIA - MÁQUINA 2   |
| (Linux CachyOS Ryzen 5 / 16 GB RAM / GPU GTX 1660S)   |  | (Contenedores Docker locales en Node 2)    |
|  - PyTorch CUDA Engine: Entrenamiento local y         |  |  - PostgreSQL Engine: Logs de auditoría,   |
|    predicciones asíncronas diarias (NVIDIA CUDA v12+) |  |    predicciones de IA y reglas de negocio  |
|  - Modelos predictivos ligeros optimizados para VRAM  |  |  - InfluxDB Engine: Velas de 5M del NASDAQ |
+-------------------------------------------------------+  +--------------------------------------------+
```

### 2.3 Decisiones de Diseño y Rationale

| ID Decisión | Concepto de Diseño | Rationale (Justificación Técnica) |
| :--- | :--- | :--- |
| **DD-01** | Procesamiento Local CUDA | Se excluyen APIs externas de IA por razones regulatorias de confidencialidad financiera de RiTech SAS. El entrenamiento de la IA se realiza en disco y GPU locales utilizando CUDA v12. |
| **DD-02** | Contenedores Docker Locales | El backend (FastAPI) y las bases de datos (PostgreSQL/InfluxDB) se compilan y ejecutan mediante `docker-compose` en la Máquina 2 (Central), el frontend de React se sirve en la Máquina 3 (Windows 11) y el modelo PyTorch se ejecuta en la Máquina 1 (GPU). Esto garantiza el aprovechamiento de recursos locales sin dependencias externas. |
| **DD-03** | InfluxDB para Ticks 5M | Los datos temporales del NASDAQ se persisten en InfluxDB local. Esto asegura consultas síncronas rápidas (menores a 100 ms) para optimizar el rendimiento y escalabilidad futura a otros índices. |
| **DD-04** | Master Kill-Switch Bloqueante | El botón de pánico en la UI envía una petición asíncrona inmediata que bloquea el hilo de trading de la IA y envía peticiones TCP síncronas bloqueantes al broker Interactive Brokers para liquidar posiciones al precio de mercado. |

---

## 3. Diseño de Datos

### 3.1 Modelo de Datos Físico (Esquema de Bases de Datos)

El sistema utilizará una aproximación híbrida de bases de datos locales:
1.  **PostgreSQL (Relacional):** Para almacenar configuraciones del sistema, usuarios, credenciales encriptadas de Interactive Brokers, predicciones históricas (con justificación de texto), transacciones reales/simuladas y registros de auditoría (logs de actividad).
2.  **InfluxDB (Series Temporales):** Estructura orientada a series temporales optimizada para el precio de mercado del NASDAQ-100 muestreado cada 5 minutos de forma cronológica.

#### 3.1.1 Tablas Físicas de PostgreSQL (Esquema relacional de auditoría)

##### Tabla `trader_users`
*   `id`: `UUID` [PK]. Identificador único de usuario.
*   `username`: `VARCHAR(50)` [UNIQUE, NOT NULL]. Nombre de usuario local.
*   `password_hash`: `VARCHAR(255)` [NOT NULL]. Hash de contraseña (Bcrypt).
*   `role`: `VARCHAR(20)` [NOT NULL]. (`ADMIN_TI` | `OPERATOR` | `GERENTE`).
*   `totp_secret`: `VARCHAR(100)`. Llave secreta para autenticación multifactor local.

##### Tabla `system_configurations`
*   `id`: `UUID` [PK].
*   `max_loss_day`: `DECIMAL(5, 2)` [NOT NULL, DEFAULT 1.25]. Porcentaje máximo tolerado de pérdida antes del Kill-switch.
*   `is_autonomous`: `BOOLEAN` [NOT NULL, DEFAULT FALSE]. Flag que habilita el Modo Autónomo.
*   `risk_profile`: `VARCHAR(20)` [NOT NULL, DEFAULT 'CONVENTIONAL']. (`CONVENTIONAL` | `MODERATE` | `AGGRESSIVE`).

##### Tabla `ai_predictions`
*   `id`: `UUID` [PK].
*   `prediction_time`: `BIGINT` [NOT NULL]. Marca de tiempo Epoch ms de la predicción.
*   `trend_prediction`: `VARCHAR(10)` [NOT NULL]. (`UP` | `DOWN` | `STABLE`).
*   `confidence_score`: `DECIMAL(5, 4)` [NOT NULL]. Probabilidad o confianza del modelo (0.0000 a 1.0000).
*   `technical_rationale`: `TEXT` [NOT NULL]. Justificación técnica y explicabilidad para auditores (XAI).

##### Tabla `transactions`
*   `id`: `UUID` [PK].
*   `prediction_id`: `UUID` [FK -> `ai_predictions.id`, NULLABLE]. Vínculo directo de auditoría con la predicción de la IA.
*   `broker_order_id`: `VARCHAR(100)` [NOT NULL]. ID único retornado por Interactive Brokers.
*   `transaction_type`: `VARCHAR(10)` [NOT NULL]. (`BUY` | `SELL`).
*   `quantity`: `DECIMAL(12, 4)` [NOT NULL]. Cantidad de contratos negociados.
*   `execution_price`: `DECIMAL(12, 4)` [NOT NULL]. Precio real de ejecución del broker.
*   `execution_time`: `BIGINT` [NOT NULL]. Marca de tiempo de ejecución (Epoch ms).
*   `is_simulated`: `BOOLEAN` [NOT NULL, DEFAULT TRUE]. Flag que indica si se ejecutó en Modo Sombra.

##### Tabla `audit_logs`
*   `id`: `UUID` [PK].
*   `timestamp`: `BIGINT` [NOT NULL]. Epoch ms del suceso.
*   `user_id`: `UUID` [FK -> `trader_users.id`, NULLABLE]. Usuario que disparó la acción.
*   `action`: `VARCHAR(100)` [NOT NULL]. Acción ejecutada (ej. `KILL_SWITCH_TRIGGERED`, `MODE_AUTONOMOUS_ENABLED`).
*   `ip_address`: `VARCHAR(45)` [NOT NULL]. Dirección IP del equipo en la intranet de RiTech.
*   `payload`: `TEXT`. Detalles técnicos en formato JSON.

#### 3.1.2 Entidad `NasdaqTick` (InfluxDB — series temporales)

Medición única por vela de 5 minutos; el *timestamp* epoch ms es la clave lógica
del punto. Alineada con SRS §4.2.2. *(Campos de rango añadidos en v1.1 — CR-001.)*

| Campo | Tipo | Descripción |
| :--- | :--- | :--- |
| `time` | Timestamp (epoch ms) | Instante de la vela; identidad lógica del punto. |
| `price_open` | Float | Precio de apertura. |
| `price_high` | Float | Precio máximo de la vela. **(v1.1)** |
| `price_low` | Float | Precio mínimo de la vela. **(v1.1)** |
| `price_close` | Float | Precio de cierre. |
| `volume` | Float | Volumen operado. |

**Regla de integridad (CR-003):** InfluxDB 2.7 **no** garantiza la inmutabilidad
— sobrescribe en silencio un punto con el mismo timestamp. La no-modificación del
histórico la impone la capa de aplicación mediante una guarda *insert-if-absent*
en `write_to_influx()`, descrita en §5.1.2.

---

## 4. Diseño UML

### 4.1 Diagrama de Casos de Uso del Sistema

```
                  +----------------------------------------------+
                  |              RiTech-NASDAQ100                |
                  |                                              |
                  |   +--------------------------------------+   |
                  |   | Iniciar Sesión / Autenticación local |   |
                  |   +------------------+-------------------+   |
                  |                      ^                       |
                  |                      |                       |
+-------------+   |   +------------------+-------------------+   |
|   Gerente   |==>|   | Autorizar Cambio a Modo Autónomo     |   |
|   General   |   |   +--------------------------------------+   |
+-------------+   |                                              |
                  |   +--------------------------------------+   |
+-------------+   |   | Visualizar Dashboard / Velas 5M      |   |
| Secretaria  |==>|   +--------------------------------------+   |
| (Operadora) |   |                                              |
+-------------+   |   +--------------------------------------+   |
                  |   | Activar Kill-Switch Maestro Manual   |   |
                  |   +--------------------------------------+   |
                  |                                              |
+-------------+   |   +--------------------------------------+   |
| Administra- |==>|   | Reentrenar IA Local CUDA             |   |
| dor de TI   |   |   +--------------------------------------+   |
+-------------+   +----------------------------------------------+
```

### 4.2 Diagrama de Secuencia: Ingesta, Predicción y Operación

```
[Proveedor API]        [IngestionEngine]        [LocalPredictiveModel]        [RiskManager]        [Interactive Brokers]
       |                       |                         |                         |                         |
       |  Cada 5 minutos       |                         |                         |                         |
       |----------------======>|                         |                         |                         |
       |  Retorna precio NASDAQ|                         |                         |                         |
       |<----------------------|                         |                         |                         |
       |                       |---=======> Guardar en   |                         |                         |
       |                       |            InfluxDB     |                         |                         |
       |                       |                         |                         |                         |
       |                       |---=====================>|                         |                         |
       |                       |   Notificar vela cerrada|                         |                         |
       |                       |                         |---=======> Evaluar IA   |                         |
       |                       |                         |            con CUDA     |                         |
       |                       |                         |                         |                         |
       |                       |                         |---=====================>|                         |
       |                       |                         |   Predicción + Confianza|                         |
       |                       |                         |                         |---=======> Validar      |
       |                       |                         |                         |            Límites      |
       |                       |                         |                         |                         |
       |                       |                         |                         |--======================>|
       |                       |                         |                         |  Enviar orden (TCP)     |
       |                       |                         |                         |                         |
```

### 4.3 Diagrama de Estados: Transición de Modos de Operación

```
        +----------------------------------------+
        |                                        |
        |             MODO ASESOR                | <------------------+
        |  - Genera recomendaciones de IA        |                    |
        |  - Alerta visual en Dashboard          |                    | Desactivación
        |  - Requiere acción humana para invertir|                    | Manual o
        |                                        |                    | Emergencia
        +-------------------+--------------------+                    |
                            |                                         |
                            | Cambio de modo                          |
                            | + Autorización Gerente                  |
                            | + Autenticación OTP                     |
                            v                                         |
        +-------------------+--------------------+                    |
        |                                        |                    |
        |             MODO AUTÓNOMO              | -------------------+
        |  - Monitoreo automático de velas       |
        |  - Colocación directa de órdenes IB     |
        |  - Validación de perfiles de riesgo    |
        |                                        |
        +----------------------------------------+
```

---

## 5. Diseño Detallado de Módulos y Clases

### 5.1 Módulo `IngestionEngine` (Python - `src/ingestion/engine.py`)
#### 5.1.1 Responsabilidad
*   Interactuar con la API externa de Alpha Vantage de forma robusta.
*   Normalizar los datos obtenidos en velas de 5 minutos.
*   Escribir las series temporales en InfluxDB de forma asíncrona.
*   **Garantizar la inmutabilidad del histórico (RF-103 / CR-003):** omitir la
    escritura de un timestamp ya persistido, ya que InfluxDB 2.7 lo
    sobrescribiría en silencio. *(v1.1)*

#### 5.1.2 Especificación técnica de la Clase

```python
class IngestionEngine:
    def __init__(self, influx_client: InfluxDBClient, api_key: str):
        self.influx_client = influx_client
        self.api_key = api_key
        self.base_url = "https://www.alphavantage.co/query"

    async def fetch_nasdaq_quote(self) -> dict:
        """Realiza la petición HTTP asíncrona para obtener la última vela de 5M."""
        pass

    def validate_payload(self, data: dict) -> bool:
        """Verifica que los datos no contengan valores nulos o ilógicos."""
        pass

    async def write_to_influx(
        self, timestamp: int, open_p: float, high_p: float, low_p: float, close_p: float, vol: float
    ) -> None:
        """Persiste la vela en el bucket de InfluxDB local de forma transaccional.

        CR-001 (v1.1): `high_p`/`low_p` añaden el rango de la vela, necesario
        para las features de rango del modelo predictivo (RF-301).
        CR-003: antes de escribir verifica que el timestamp no exista ya
        (guarda *insert-if-absent*); si existe, no escribe y emite WARNING.
        """
        pass

    async def existing_timestamps(self, timestamps: list[int]) -> set[int]:
        """Devuelve qué timestamps del lote ya existen — una sola consulta de rango."""
        pass

    async def write_candles_batch(self, candles: list) -> tuple[int, int]:
        """Escribe un lote de velas omitiendo las existentes (backfill). Devuelve (escritas, omitidas)."""
        pass
```

### 5.2 Módulo `AsymmetricCalculator` (Python - `src/calculator/asymmetric.py`)
#### 5.2.1 Responsabilidad
*   Aplicar el factor de apalancamiento 2x para calcular los retornos de inversión simulados o reales.
*   Evaluar el porcentaje de variación de la sesión frente al precio de entrada para activar las medidas de contención.

#### 5.2.2 Especificación técnica de la Clase

```python
class AsymmetricCalculator:
    @staticmethod
    def calculate_yield(nasdaq_variation_pct: float) -> float:
        """
        Retorna el rendimiento de RiTech. 
        Si es positivo, multiplica por 2 (2x). 
        Si es negativo, evalúa la pérdida.
        """
        if nasdaq_variation_pct >= 0:
            return nasdaq_variation_pct * 2.0
        else:
            return nasdaq_variation_pct

    @staticmethod
    def is_threshold_breached(entry_price: float, current_price: float) -> bool:
        """Verifica de forma estricta si la pérdida supera el 1.25%."""
        variation = ((current_price - entry_price) / entry_price) * 100
        return variation <= -1.25
```

### 5.3 Módulo `LocalPredictiveModel` (Python - `src/ai/model.py`)
#### 5.3.1 Responsabilidad
*   Cargar el modelo predictivo basado en PyTorch.
*   Ejecutar las inferencias en la GPU local utilizando soporte CUDA.
*   Retornar la predicción de tendencia, el porcentaje de confianza y justificar la lógica mediante indicadores técnicos (XAI).

#### 5.3.2 Especificación técnica de la Clase

```python
class LocalPredictiveModel:
    def __init__(self, model_path: str):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = self.load_model(model_path)

    def load_model(self, path: str) -> nn.Module:
        """Carga los pesos de la red neuronal directamente en la GPU local."""
        pass

    def run_inference(self, feature_tensor: torch.Tensor) -> tuple[str, float, str]:
        """
        Ejecuta la predicción y retorna:
        (trend_prediction: 'UP'|'DOWN'|'STABLE', confidence_score, rationale_text)
        """
        pass
```

### 5.4 Módulo `RiskManager` (Python - `src/security/risk_manager.py`)
#### 5.4.1 Responsabilidad
*   Evaluar que las decisiones cumplan con los perfiles de riesgo configurados por RiTech.
*   Administrar y ejecutar síncronamente el Kill-Switch de la plataforma.

#### 5.4.2 Especificación técnica de la Clase

```python
class RiskManager:
    def __init__(self, config_repo: dict, kill_switch_engine: KillSwitchEngine):
        self.config_repo = config_repo
        self.kill_switch = kill_switch_engine

    def evaluate_order_risk(self, prediction_confidence: float, active_profile: str) -> bool:
        """Valida si la confianza es suficiente según el perfil de riesgo activo."""
        thresholds = {
            "CONVENTIONAL": 0.85,  # Requiere alta certidumbre
            "MODERATE": 0.75,      # Umbral por defecto
            "AGGRESSIVE": 0.65     # Mayor tolerancia al riesgo
        }
        required_conf = thresholds.get(active_profile, 0.75)
        return prediction_confidence >= required_conf

    def trigger_emergency_kill(self, user_id: str) -> None:
        """Dispara de manera síncrona el Kill-switch maestro."""
        self.kill_switch.execute_global_liquidation(user_id)
```

---

## 6. Consideraciones de Calidad (ISO/IEC 25010)

### 6.1 Mantenibilidad
*   **Estructura del Proyecto:** El proyecto sigue una separación clara de responsabilidades dividiéndose en: `components/` (Frontend), `hooks/` (Frontend state), `endpoints/` (FastAPI), `ai/` (Lógica de IA PyTorch), `lib/db.ts` o `database/` (Conexiones PostgreSQL/InfluxDB).
*   **Modificabilidad:** Cambiar de proveedor de datos de NASDAQ solo requiere la modificación de la clase `IngestionEngine`, sin que tenga ningún tipo de impacto sobre la lógica de la UI o del motor predictivo.

### 6.2 Rendimiento
*   **Queries Optimizadas:** La consulta de series temporales se realiza sobre InfluxDB local mediante su lenguaje de consulta estructurado `Flux`, retornando agregaciones directamente procesadas en base de datos para no saturar la memoria del servidor de la oficina.

### 6.3 Usabilidad
*   **Diseño para Secretaria:** La pantalla de Dashboard contará con un panel visual simplificado e intuitivo, mostrando el estado actual en color verde (Inversión Normal), amarillo (Mercado Inestable) y rojo parpadeante (Kill-Switch Activo), asegurando la facilidad operativa con nula fatiga cognitiva para el usuario.

### 6.4 Seguridad
*   **Ley de Habeas Data:** Dado que es un software on-premise local sin nube, se cumple de manera inherente con la legislación de protección de datos colombiana al no existir la transmisión de datos financieros transaccionales sensibles hacia servidores transfronterizos.

---

## 7. Reglas Internas de Diseño y Convenciones

1.  **Strict Typing:** Prohibida la utilización de tipos dinámicos implícitos en Python y TypeScript. Todo endpoint de FastAPI debe tipar sus parámetros mediante esquemas `Pydantic`.
2.  **Identificadores Únicos:** Toda entidad física almacenada en PostgreSQL debe usar UUIDs generados localmente mediante la función `crypto.randomUUID()` en frontend o `uuid.uuid4()` en backend para evitar la predictibilidad de claves secuenciales.
3.  **Logs de Auditoría:** Está prohibido que una clase de negocio de base de datos realice transacciones de trading o cambios de perfiles de riesgo sin insertar síncronamente su respectivo log en la tabla `audit_logs` con IP interna de la intranet.

---

## 8. Trazabilidad del Código

Para verificar que el diseño técnico satisface todos los requisitos del SRS v1.1, se presenta la siguiente matriz de trazabilidad:

| ID Requisito (SRS) | Clase / Módulo del Diseño (SDD) | Archivo en la Estructura |
| :--- | :--- | :--- |
| **RF-101** | `IngestionEngine` | `src/ingestion/engine.py` |
| **RF-102** | `IngestionEngine` | `src/ingestion/engine.py` |
| **RF-103** | `IngestionEngine` / InfluxDB | `src/ingestion/engine.py` |
| **RF-201** | `AsymmetricCalculator` | `src/calculator/asymmetric.py` |
| **RF-202** | `AsymmetricCalculator` | `src/calculator/asymmetric.py` |
| **RF-203** | `AsymmetricCalculator` / Error Handling | `src/calculator/asymmetric.py` |
| **RF-301** | `LocalPredictiveModel` | `src/ai/model.py` |
| **RF-302** | `LocalPredictiveModel` (Scheduler) | `src/ai/model.py` |
| **RF-303** | `LocalPredictiveModel` (XAI Output) | `src/ai/model.py` |
| **RF-401** | UI Dashboard / State Router | `src/pages/Dashboard.tsx` |
| **RF-402** | `BrokerGateway` / `RiskManager` | `src/integration/broker.py` |
| **RF-501** | `KillSwitchEngine` / UI UI Dashboard | `src/pages/Dashboard.tsx` |
| **RF-502** | `KillSwitchEngine` / síncrono | `src/security/kill_switch.py` |
| **RF-601** | `BrokerGateway` (Sandbox config) | `src/integration/broker.py` |
| **RF-701** | PostgreSQL Table `transactions` | `src/database/schema.sql` |
| **RF-702** | `ExportUtility` (CSV/PDF) | `src/utils/export.py` |
