-- ===========================================================================
-- Esquema físico de PostgreSQL — RiTech-NASDAQ100
-- Fuente: docs/sdd.md §3.1.1 (SDD v1.1). Ninguna columna se inventa aquí.
--
-- **IDEMPOTENTE.** Todo usa `IF NOT EXISTS`, así que aplicarlo dos veces es
-- inocuo y no destruye datos. Hace falta porque el volumen de PostgreSQL ya
-- existe: `initdb.d` solo se ejecuta en el primer arranque del contenedor y
-- NO vuelve a correr, así que cualquier tabla nueva necesita este esquema y un
-- comando de aplicación explícito (ver `docs/despliegue.md`).
--
-- Aplicar con:
--   docker exec -i ritech-postgres16 psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
--     < src/database/schema.sql
--
-- Nota sobre UUID: `gen_random_uuid()` es nativo desde PostgreSQL 13; aquí corre
-- PostgreSQL 16, así que NO se necesita pgcrypto. La convención del proyecto es
-- UUID generado localmente y nunca ID secuencial predecible (AGENTS.md §5.2).
--
-- Retención: el SRS exige retener los datos transaccionales y de auditoría
-- **5 años**. Esa retención es una política operativa (respaldos y purga), no
-- algo que pueda imponer el DDL; queda documentada aquí y en el runbook.
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- trader_users — usuarios locales de la plataforma
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trader_users (
    id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    username      VARCHAR(50)  NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    role          VARCHAR(20)  NOT NULL,
    totp_secret   VARCHAR(100),
    CONSTRAINT trader_users_role_valido
        CHECK (role IN ('ADMIN_TI', 'OPERATOR', 'GERENTE'))
);

COMMENT ON TABLE  trader_users IS 'Usuarios locales; autenticación y MFA sin dependencia de la nube (REST-01).';
COMMENT ON COLUMN trader_users.totp_secret IS 'Llave TOTP para el OTP del Modo Autónomo (SRS §4.1).';


-- ---------------------------------------------------------------------------
-- system_configurations — configuración activa del sistema
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS system_configurations (
    id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    max_loss_day  DECIMAL(5,2) NOT NULL DEFAULT 1.25,
    is_autonomous BOOLEAN      NOT NULL DEFAULT FALSE,
    risk_profile  VARCHAR(20)  NOT NULL DEFAULT 'CONVENTIONAL',
    CONSTRAINT system_configurations_perfil_valido
        CHECK (risk_profile IN ('CONVENTIONAL', 'MODERATE', 'AGGRESSIVE')),
    CONSTRAINT system_configurations_max_loss_positivo
        CHECK (max_loss_day > 0)
);

COMMENT ON COLUMN system_configurations.max_loss_day IS 'Techo teórico de pérdida diaria en % (Motor Asimétrico, RF-202).';
COMMENT ON COLUMN system_configurations.is_autonomous IS 'Solo la Gerente General puede pasarlo a TRUE (RF-401).';


-- ---------------------------------------------------------------------------
-- ai_predictions — predicciones de la IA local (RF-301 a RF-304)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ai_predictions (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    prediction_time     BIGINT       NOT NULL,
    trend_prediction    VARCHAR(10)  NOT NULL,
    confidence_score    DECIMAL(5,4) NOT NULL,
    technical_rationale TEXT         NOT NULL,
    CONSTRAINT ai_predictions_tendencia_valida
        CHECK (trend_prediction IN ('UP', 'DOWN', 'STABLE')),
    -- RF-304: el ratio de confianza es una probabilidad entre 0 y 1.
    CONSTRAINT ai_predictions_confianza_rango
        CHECK (confidence_score >= 0.0000 AND confidence_score <= 1.0000),
    -- XAI vacío sería una predicción sin justificación, y RF-303 exige la
    -- justificación para auditoría. Mejor rechazar la fila que guardar un
    -- registro que no sirve a nadie.
    CONSTRAINT ai_predictions_rationale_no_vacio
        CHECK (length(btrim(technical_rationale)) > 0)
);

-- Un índice ÚNICO (y no uno simple): el ciclo solo puede escribir UNA predicción
-- por vela, y `prediction_time` es el cierre de la última vela de la ventana, no
-- el reloj (decisión D2): es determinista, sobrevive a un reinicio del scheduler
-- y es lo que permite el `ON CONFLICT` de insert-if-absent y calcular después el
-- "resultado real". El índice no único del Sprint 2 (`idx_ai_predictions_time`)
-- queda absorbido: se retira con IF EXISTS para no dejar duplicado en la BD ya
-- aplicada.
DROP INDEX IF EXISTS idx_ai_predictions_time;

CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_predictions_time
    ON ai_predictions (prediction_time);

COMMENT ON TABLE  ai_predictions IS 'Predicciones persistidas por M2 desde el cliente de inferencia; M1 no escribe en PostgreSQL.';
COMMENT ON COLUMN ai_predictions.prediction_time IS 'Epoch ms del cierre de la ÚLTIMA VELA de la ventana de 12 usada para decidir (no el reloj).';
COMMENT ON COLUMN ai_predictions.confidence_score IS 'Posterior corregido por prior (0.0000 a 1.0000), NO la softmax cruda — ver src/ai/client.py.';
COMMENT ON COLUMN ai_predictions.technical_rationale IS 'Atribución XAI en lenguaje financiero (RF-303). NO es una causalidad de mercado.';


-- ---------------------------------------------------------------------------
-- transactions — órdenes reales y simuladas (Modo Sombra)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS transactions (
    id               UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    prediction_id    UUID,
    broker_order_id  VARCHAR(100)  NOT NULL,
    transaction_type VARCHAR(10)   NOT NULL,
    quantity         DECIMAL(12,4) NOT NULL,
    execution_price  DECIMAL(12,4) NOT NULL,
    execution_time   BIGINT        NOT NULL,
    is_simulated     BOOLEAN       NOT NULL DEFAULT TRUE,
    CONSTRAINT transactions_prediction_fk
        FOREIGN KEY (prediction_id) REFERENCES ai_predictions (id) ON DELETE SET NULL,
    CONSTRAINT transactions_tipo_valido
        CHECK (transaction_type IN ('BUY', 'SELL')),
    CONSTRAINT transactions_cantidad_positiva
        CHECK (quantity > 0),
    CONSTRAINT transactions_precio_no_negativo
        CHECK (execution_price >= 0)
);

CREATE INDEX IF NOT EXISTS idx_transactions_time
    ON transactions (execution_time DESC);
CREATE INDEX IF NOT EXISTS idx_transactions_prediction
    ON transactions (prediction_id);

COMMENT ON COLUMN transactions.is_simulated IS 'Modo Sombra (RF-602): TRUE en Sandbox, estrictamente separado de lo real.';


-- ---------------------------------------------------------------------------
-- audit_logs — trazabilidad de acciones críticas (retención 5 años)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_logs (
    id         UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp  BIGINT       NOT NULL,
    user_id    UUID,
    action     VARCHAR(100) NOT NULL,
    -- CR-005: QUIÉN actuó. Sin DEFAULT permanente a propósito: si el valor se
    -- olvida la fila no se inserta, y un log sin actor no prueba nada. Los
    -- tres valores son los que existen hoy (sin autenticación de usuarios,
    -- CR-006): el scheduler es SISTEMA, un disparo manual es OPERADOR y el
    -- reinicio con clave es CLAVE_REINICIO.
    actor      VARCHAR(50)  NOT NULL,
    ip_address VARCHAR(45)  NOT NULL,
    payload    TEXT,
    CONSTRAINT audit_logs_user_fk
        FOREIGN KEY (user_id) REFERENCES trader_users (id) ON DELETE SET NULL,
    CONSTRAINT audit_logs_actor_valido
        CHECK (actor IN ('SISTEMA', 'OPERADOR', 'CLAVE_REINICIO'))
    -- La IP es NOT NULL porque el SRS §4.2.5 la exige y el AGENTS.md §5.3
    -- obliga a registrarla en cada acción de negocio. Sin ella el registro de
    -- auditoría no prueba nada sobre quién ordenó qué desde dónde.
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_time
    ON audit_logs (timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_action
    ON audit_logs (action);

COMMENT ON COLUMN audit_logs.ip_address IS 'IP interna del equipo en la intranet de RiTech (AGENTS.md §5.3).';
COMMENT ON COLUMN audit_logs.payload IS 'Detalles técnicos en JSON; puede incluir la configuración previa a un cambio.';


-- ===========================================================================
-- Inmutabilidad de `audit_logs` A NIVEL DE BASE DE DATOS (RF-701)
-- ===========================================================================
-- La lección del insert-if-absent de InfluxDB (CR-003): el comportamiento del
-- motor NO se asume, se verifica contra el motor real. Aquí la garantía no
-- la da la capa de aplicación sino PostgreSQL:
--
--   * UPDATE y DELETE se rechazan fila a fila;
--   * TRUNCATE se rechaza a nivel de sentencia, porque los triggers por fila
--     NO se disparan ante un TRUNCATE (hace falta un trigger de sentencia);
--   * ambos quedan en ENABLE ALWAYS;
--   * y los privilegios de modificación se revocan como segunda muralla.
--
-- Riesgo residual, documentado y no eliminado: el servicio entra como
-- superusuario, que SÍ salta los permisos y SÍ puede ejecutar
-- `ALTER TABLE ... DISABLE TRIGGER`. Los triggers, en cambio, se disparan
-- también para el superusuario. Dejar de entrar como superusuario (rol de
-- aplicación de mínimo privilegio) queda como trabajo futuro: CR-006.

-- ---------------------------------------------------------------------------
-- Migración idempotente de `actor` para el volumen que ya existe
-- ---------------------------------------------------------------------------
-- Va ANTES de los triggers porque su backfill es un UPDATE: con el trigger
-- puesto, la propia migración se rechazaría. El cuerpo solo se ejecuta si la
-- columna no estaba, así que la segunda aplicación del archivo no vuelve a
-- tocar ninguna fila (y por eso tampoco se dispara el trigger).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'audit_logs'
          AND column_name = 'actor'
    ) THEN
        ALTER TABLE audit_logs ADD COLUMN actor VARCHAR(50);
        UPDATE audit_logs SET actor = 'SISTEMA' WHERE actor IS NULL;
        ALTER TABLE audit_logs ALTER COLUMN actor SET NOT NULL;
        ALTER TABLE audit_logs ADD CONSTRAINT audit_logs_actor_valido
            CHECK (actor IN ('SISTEMA', 'OPERADOR', 'CLAVE_REINICIO'));
    END IF;
    -- Sin default permanente: quien escribe el log tiene que decir quién. Es
    -- un no-op si nunca lo hubo, así que puede correr en cada aplicación.
    ALTER TABLE audit_logs ALTER COLUMN actor DROP DEFAULT;
END $$;

-- Va DESPUÉS del bloque DO: en el volumen ya existente la columna recién
-- existe ahí, y un COMMENT sobre una columna inexistente es un error que
-- detendría el resto del archivo con ON_ERROR_STOP.
COMMENT ON COLUMN audit_logs.actor IS 'Quién actuó: SISTEMA, OPERADOR o CLAVE_REINICIO (CR-005).';


-- ---------------------------------------------------------------------------
-- kill_switch_state — estado persistido del Kill-Switch (RF-501, CR-005)
-- ---------------------------------------------------------------------------
-- RF-501 pide un bloqueo que sobreviva a un reinicio del proceso: la bandera
-- en memoria corta al instante, pero si el servicio se cae o se reinicia el
-- estado tiene que seguir estando. Por eso el estado vive aquí y no solo en
-- `src/security/kill_switch.py`.
--
-- Singleton por convención, igual que `system_configurations`: la fila la
-- siembra Python (`sembrar_si_vacio`, al arrancar el API y el scheduler) y la
-- lectura ordena `activo DESC` —si una carrera dejara dos filas, manda la que
-- está activa, es decir, se falla cerrado—.
--
-- La idempotencia del disparo NO se busca aquí: sale de un compare-and-set
-- (`UPDATE ... WHERE activo = FALSE RETURNING`) en la capa de aplicación.
CREATE TABLE IF NOT EXISTS kill_switch_state (
    id                 UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    -- FALSE = sistema operable. La bandera en memoria es la que corta antes;
    -- esta columna es la verdad compartida entre procesos.
    activo             BOOLEAN      NOT NULL DEFAULT FALSE,
    motivo             VARCHAR(200) NOT NULL,
    -- MANUAL = pulsado desde el panel; AUTOMATICO = disparado por el umbral
    -- de pérdida de sesión (RF-202 / RF-503).
    origen             VARCHAR(20)  NOT NULL,
    -- Quién disparó la activación actual. El reinicio se audita aparte, con
    -- actor CLAVE_REINICIO, en `audit_logs`.
    actor              VARCHAR(50)  NOT NULL,
    disparado_en       BIGINT       NOT NULL,
    -- Recorrido de la liquidación: un proceso que muere a mitad deja
    -- PENDIENTE/EN_CURSO visible en el panel en lugar de un bloqueo mudo.
    liquidacion_estado VARCHAR(20)  NOT NULL DEFAULT 'NINGUNA',
    liquidacion_en     BIGINT,
    -- Acumulado entre activaciones: es lo que permite probar que pulsar dos
    -- veces no liquidó dos veces.
    liquidaciones      INTEGER      NOT NULL DEFAULT 0,
    reinicio_en        BIGINT,
    -- Fecha ET del último reinicio: tras reiniciar, el disparo automático no
    -- se vuelve a evaluar hasta la siguiente sesión (evita bucles).
    sesion_reinicio    DATE,
    -- Límite de intentos de la clave de reinicio (CR-006), con su ventana.
    intentos_reinicio  INTEGER      NOT NULL DEFAULT 0,
    ultimo_intento_en  BIGINT       NOT NULL DEFAULT 0,
    CONSTRAINT kill_switch_origen_valido
        CHECK (origen IN ('MANUAL', 'AUTOMATICO')),
    CONSTRAINT kill_switch_actor_valido
        CHECK (actor IN ('SISTEMA', 'OPERADOR')),
    CONSTRAINT kill_switch_liquidacion_valida
        CHECK (liquidacion_estado IN
               ('NINGUNA', 'PENDIENTE', 'EN_CURSO', 'COMPLETADA', 'FALLIDA')),
    CONSTRAINT kill_switch_intentos_no_negativos
        CHECK (intentos_reinicio >= 0)
);

COMMENT ON TABLE  kill_switch_state IS 'Estado persistido del Kill-Switch maestro (RF-501); una sola fila (CR-005).';
COMMENT ON COLUMN kill_switch_state.liquidaciones IS 'Liquidaciones acumuladas: 2 tras pulsar dos veces = fallo de idempotencia.';
COMMENT ON COLUMN kill_switch_state.sesion_reinicio IS 'Fecha ET del reinicio; el disparo automático espera a la siguiente sesión.';
COMMENT ON COLUMN kill_switch_state.intentos_reinicio IS 'Intentos fallidos de clave de reinicio dentro de la ventana (CR-006).';


-- ---------------------------------------------------------------------------
-- Trigger de fila: rechaza UPDATE y DELETE
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION audit_logs_rechazar_modificacion() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'audit_logs es inmutable: % sobre % no está permitido (RF-701).',
        TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'P0001';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_logs_no_modificar ON audit_logs;
CREATE TRIGGER audit_logs_no_modificar
    BEFORE UPDATE OR DELETE ON audit_logs
    FOR EACH ROW
    EXECUTE FUNCTION audit_logs_rechazar_modificacion();

-- ENABLE ALWAYS: el trigger corre también en sesión de réplica, donde por
-- defecto los triggers de fila se silencian.
ALTER TABLE audit_logs ENABLE ALWAYS TRIGGER audit_logs_no_modificar;


-- ---------------------------------------------------------------------------
-- Trigger de sentencia: rechaza TRUNCATE (el de fila no lo alcanza)
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION audit_logs_rechazar_truncado() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION
        'audit_logs es inmutable: el vaciado completo de la tabla no está permitido (RF-701).'
        USING ERRCODE = 'P0001';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_logs_no_vaciar ON audit_logs;
CREATE TRIGGER audit_logs_no_vaciar
    BEFORE TRUNCATE ON audit_logs
    FOR EACH STATEMENT
    EXECUTE FUNCTION audit_logs_rechazar_truncado();

ALTER TABLE audit_logs ENABLE ALWAYS TRIGGER audit_logs_no_vaciar;


-- ---------------------------------------------------------------------------
-- Segunda muralla: sin privilegios de modificación
-- ---------------------------------------------------------------------------
-- INSERT sigue permitido (es lo único que hace el sistema) y SELECT también
-- (la auditoría tiene que poder leerse). UPDATE, DELETE y el vaciado completo
-- se revocan de PUBLIC; el dueño de la tabla (hoy el superusuario del
-- servicio) queda cubierto por los triggers de arriba.
REVOKE UPDATE, DELETE, TRUNCATE ON audit_logs FROM PUBLIC;

