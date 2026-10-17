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

CREATE INDEX IF NOT EXISTS idx_ai_predictions_time
    ON ai_predictions (prediction_time DESC);

COMMENT ON TABLE  ai_predictions IS 'Predicciones persistidas por M2 desde el cliente de inferencia; M1 no escribe en PostgreSQL.';
COMMENT ON COLUMN ai_predictions.prediction_time IS 'Epoch ms del cierre de la ventana de 12 velas usada para decidir.';
COMMENT ON COLUMN ai_predictions.confidence_score IS 'Probabilidad softmax de la clase ganadora (0.0000 a 1.0000).';
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
    ip_address VARCHAR(45)  NOT NULL,
    payload    TEXT,
    CONSTRAINT audit_logs_user_fk
        FOREIGN KEY (user_id) REFERENCES trader_users (id) ON DELETE SET NULL
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
