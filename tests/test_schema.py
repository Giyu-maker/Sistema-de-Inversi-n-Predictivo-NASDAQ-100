"""Pruebas del esquema físico de PostgreSQL (`src/database/schema.sql`).

La parte viva necesita el contenedor, así que se omite si no hay conexión. La
parte estructural corre siempre y protege lo que sí se rompe por accidente:
que alguien meta un `DROP` o una tabla sin `IF NOT EXISTS` y deje de ser
idempotente. Hace falta porque el volumen de PostgreSQL ya existe y
`initdb.d` no vuelve a ejecutarse: un esquema no idempotente obligaría a
vaciar la base para aplicarlo, perdiendo la auditoría de 5 años.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

SCHEMA = Path(__file__).resolve().parents[1] / "src" / "database" / "schema.sql"
_sql = SCHEMA.read_text(encoding="utf-8")
# Se ignoran los comentarios: el archivo documenta cada decisión al lado del DDL.
_lineas = [l for l in _sql.splitlines() if not l.strip().startswith("--")]
SQL = "\n".join(_lineas)

TABLAS = {
    "trader_users", "system_configurations", "ai_predictions", "transactions",
    "audit_logs", "kill_switch_state",
}


class TestIdempotencia:
    def test_el_archivo_existe_y_no_esta_vacio(self) -> None:
        assert SCHEMA.is_file() and len(SQL.strip()) > 500

    def test_toda_tabla_declara_create_table_if_not_exists(self) -> None:
        creadas = set(re.findall(r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?(\w+)", SQL))
        assert creadas == TABLAS
        sin_guardia = re.findall(r"CREATE TABLE\s+(?!IF NOT EXISTS)(\w+)", SQL)
        assert sin_guardia == [], f"tablas sin IF NOT EXISTS: {sin_guardia}"

    def test_todos_los_indices_declaran_if_not_exists(self) -> None:
        # `CREATE UNIQUE INDEX` es un índice también: sin el `(?:UNIQUE )?` la
        # regla dejaría pasar al único que más fácil sería escribir mal.
        sin_guardia = re.findall(r"CREATE (?:UNIQUE )?INDEX\s+(?!IF NOT EXISTS)(\w+)", SQL)
        assert sin_guardia == [], f"índices sin IF NOT EXISTS: {sin_guardia}"
        assert len(re.findall(r"CREATE (?:UNIQUE )?INDEX IF NOT EXISTS", SQL)) == 5

    def test_no_hay_sentencias_destructivas(self) -> None:
        # DROP/TRUNCATE/ALTER ... CASCADE vaciarían la auditoría de 5 años.
        # Se protegen TABLAS: `DROP INDEX IF EXISTS` (D2, retirada del índice
        # no único absorbido por el único) no toca datos y debe poder existir.
        destructivas = re.findall(
            r"\b(DROP\s+TABLE|DROP\s+SCHEMA|ALTER\s+TABLE\s+\w+\s+COLUMN)\b",
            SQL, flags=re.I,
        )
        # `TRUNCATE` solo como sentencia al principio de línea: el `REVOKE` de
        # la sección de inmutabilidad lo nombra como privilegio que se RETIRA,
        # que es justo lo contrario de vaciar la tabla.
        destructivas += re.findall(r"^\s*TRUNCATE\b", SQL, flags=re.I | re.M)
        assert destructivas == [], f"sentencias destructivas: {destructivas}"

    def test_no_hay_datos_insertados_de_fabrica(self) -> None:
        # Los usuarios y la configuración los crea quien despliega, no el DDL.
        assert re.search(r"\bINSERT\s+INTO\b", SQL, flags=re.I) is None


class TestAiPredictions:
    """RF-301 a RF-304 — la tabla debe coincidir literalmente con SDD §3.1.1."""

    def _bloque(self) -> str:
        m = re.search(r"CREATE TABLE IF NOT EXISTS ai_predictions\s*\((.*?)\n\);", SQL, re.S)
        assert m, "no se encontró la definición de ai_predictions"
        return m.group(1)

    def test_tiene_exactamente_las_columnas_del_sdd(self) -> None:
        # Se ignoran las líneas de CONSTRAINT: son restricciones, no columnas.
        columnas = set(re.findall(
            r"^\s{4}(\w+)\s", self._bloque(), re.M
        )) - {"CONSTRAINT"}
        assert columnas == {
            "id", "prediction_time", "trend_prediction",
            "confidence_score", "technical_rationale",
        }

    def test_tendencia_solo_admite_up_down_stable(self) -> None:
        bloque = self._bloque()
        assert "CHECK (trend_prediction IN ('UP', 'DOWN', 'STABLE'))" in bloque

    def test_confianza_esta_limitada_a_cero_uno(self) -> None:
        bloque = self._bloque()
        assert "confidence_score >= 0.0000 AND confidence_score <= 1.0000" in bloque
        assert re.search(r"confidence_score\s+DECIMAL\(5,\s*4\)", bloque)

    def test_prediction_time_es_epoch_ms_entero(self) -> None:
        assert re.search(r"prediction_time\s+BIGINT", self._bloque())

    def test_el_rationale_no_puede_quedar_vacio(self) -> None:
        # RF-303: una predicción sin justificación no sirve a un auditor.
        assert "ai_predictions_rationale_no_vacio" in self._bloque()
        assert "length(btrim(technical_rationale)) > 0" in self._bloque()

    def test_id_es_uuid_con_generador_local(self) -> None:
        # AGENTS.md §5.2: UUID local, nunca ID secuencial predecible.
        bloque = self._bloque()
        assert re.search(r"id\s+UUID\s+PRIMARY KEY DEFAULT gen_random_uuid\(\)", bloque)
        assert re.search(r"\bSERIAL\b|\bIDENTITY\b", SQL, flags=re.I) is None


class TestOtrasTablas:
    def test_foreign_key_de_transactions_apunta_a_ai_predictions(self) -> None:
        assert re.search(
            r"FOREIGN KEY \(prediction_id\) REFERENCES ai_predictions \(id\)", SQL
        )

    def test_transaction_type_solo_admite_buy_sell(self) -> None:
        assert "CHECK (transaction_type IN ('BUY', 'SELL'))" in SQL

    def test_is_simulated_es_true_por_defecto(self) -> None:
        # Modo Sombra (RF-602): por defecto se simula, nunca real.
        assert re.search(r"is_simulated\s+BOOLEAN\s+NOT NULL DEFAULT TRUE", SQL)

    def test_el_ip_de_auditoria_es_obligatoria(self) -> None:
        # AGENTS.md §5.3: sin la IP, el registro no prueba quién ordenó qué.
        bloque = re.search(r"CREATE TABLE IF NOT EXISTS audit_logs\s*\((.*?)\n\);", SQL, re.S)
        assert bloque
        assert re.search(r"ip_address\s+VARCHAR\(45\)\s+NOT NULL", bloque.group(1))

    def test_el_perfil_de_riesgo_tiene_sus_tres_valores(self) -> None:
        assert "CHECK (risk_profile IN ('CONVENTIONAL', 'MODERATE', 'AGGRESSIVE'))" in SQL
        assert re.search(r"risk_profile\s+VARCHAR\(20\)\s+NOT NULL DEFAULT 'CONVENTIONAL'", SQL)

    def test_max_loss_day_es_el_techo_por_defecto_del_srs(self) -> None:
        assert re.search(r"max_loss_day\s+DECIMAL\(5,\s*2\)\s+NOT NULL DEFAULT 1\.25", SQL)

    def test_los_indices_cubren_los_filtrados_del_dashboard(self) -> None:
        for indice in ("idx_transactions_time", "idx_audit_logs_time",
                       "idx_audit_logs_action"):
            assert f"CREATE INDEX IF NOT EXISTS {indice}" in SQL

    def test_prediction_time_es_unico_por_vela(self) -> None:
        # D2: una sola predicción por vela. Es lo que hace seguro el
        # insert-if-absent del ciclo y lo que permite derivar el "resultado
        # real" sin temer filas repetidas tras un reinicio del scheduler.
        assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_predictions_time" in SQL
        assert "ON ai_predictions (prediction_time)" in SQL
        # El índice no único del Sprint 2 queda absorbido, no duplicado.
        assert "DROP INDEX IF EXISTS idx_ai_predictions_time" in SQL
        assert "CREATE INDEX IF NOT EXISTS idx_ai_predictions_time" not in SQL


class TestInmutabilidad:
    """RF-701 — la inmutabilidad vive en el DDL, no en la capa de aplicación."""

    def test_esta_el_trigger_que_rechaza_update_y_delete(self) -> None:
        assert "CREATE TRIGGER audit_logs_no_modificar" in SQL
        assert "BEFORE UPDATE OR DELETE ON audit_logs" in SQL
        assert "FOR EACH ROW" in SQL
        assert "audit_logs_rechazar_modificacion()" in SQL

    def test_esta_el_trigger_de_sentencia_para_el_vaciado(self) -> None:
        # Los triggers por fila NO se disparan ante un TRUNCATE: sin este,
        # un `TRUNCATE audit_logs` vaciaría los 5 años de auditoría.
        assert "CREATE TRIGGER audit_logs_no_vaciar" in SQL
        assert "BEFORE TRUNCATE ON audit_logs" in SQL
        assert "FOR EACH STATEMENT" in SQL

    def test_los_dos_triggers_estan_enable_always(self) -> None:
        assert len(re.findall(r"ENABLE ALWAYS TRIGGER", SQL)) == 2
        assert "ENABLE TRIGGER" not in SQL.replace("ENABLE ALWAYS TRIGGER", "")

    def test_se_revocan_los_privilegios_de_modificacion(self) -> None:
        # Segunda muralla: ni UPDATE, ni DELETE, ni el vaciado completo.
        assert re.search(
            r"REVOKE\s+UPDATE,\s*DELETE,\s*TRUNCATE\s+ON\s+audit_logs\s+FROM\s+PUBLIC",
            SQL, flags=re.I,
        )
        # INSERT y SELECT siguen permitidos: hay que poder escribir y leer.
        assert not re.search(r"REVOKE\s+INSERT", SQL, flags=re.I)
        assert not re.search(r"REVOKE\s+SELECT", SQL, flags=re.I)

    def test_las_sentencias_lanzan_excepcion(self) -> None:
        assert SQL.count("RAISE EXCEPTION") == 2
        assert "USING ERRCODE" in SQL

    def test_la_migracion_del_actor_va_antes_de_los_triggers(self) -> None:
        # El backfill de `actor` es un UPDATE: con el trigger ya creado, la
        # propia migración se rechazaría y el esquema no se podría aplicar.
        migracion = SQL.index("DO $$")
        trigger = SQL.index("CREATE TRIGGER audit_logs_no_modificar")
        assert migracion < trigger, "el UPDATE de migración va después del trigger"

    def test_el_comentario_del_actor_va_despues_de_la_migracion(self) -> None:
        # En el volumen existente la columna recién existe dentro del DO: un
        # COMMENT anterior es un error que, con ON_ERROR_STOP, deja el resto
        # del archivo (triggers y REVOKE) sin aplicar.
        migracion = SQL.index("DO $$")
        comentario = SQL.index("COMMENT ON COLUMN audit_logs.actor")
        assert migracion < comentario, "el COMMENT del actor va antes de crear la columna"

    def test_actor_esta_declarado_en_la_tabla(self) -> None:
        bloque = re.search(r"CREATE TABLE IF NOT EXISTS audit_logs\s*\((.*?)\n\);", SQL, re.S)
        assert bloque
        assert re.search(r"actor\s+VARCHAR\(50\)\s+NOT NULL", bloque.group(1))
        assert "CHECK (actor IN ('SISTEMA', 'OPERADOR', 'CLAVE_REINICIO'))" in SQL

    def test_actor_no_tiene_default_permanente(self) -> None:
        # Sin default: si quien escribe olvida el actor, la fila no se inserta.
        assert re.search(r"actor\s+VARCHAR\(50\)\s+NOT NULL\s+DEFAULT", SQL) is None
        assert "ALTER COLUMN actor DROP DEFAULT" in SQL


#: Marcador que dejan las pruebas vivas de auditoría. Se usa en `payload`, que
#: es una columna de texto libre: nada del sistema escribe ese valor.
MARCA_AUDITORIA = "TEST-AUDITORIA-INMUTABLE"


def _psql(sql: str, *args: str) -> subprocess.CompletedProcess[str]:
    """`psql` dentro del contenedor, con ON_ERROR_STOP (a nivel de módulo para
    que lo usen todas las clases vivas, no solo la de idempotencia)."""
    entorno = os.environ.copy()
    usuario = entorno.get("POSTGRES_USER", "ritech")
    base = entorno.get("POSTGRES_DB", "ritech")
    return subprocess.run(
        ["docker", "exec", "-i", "ritech-postgres16", "psql",
         "-U", usuario, "-d", base, "-v", "ON_ERROR_STOP=1", *args],
        input=sql, capture_output=True, text=True, timeout=60,
    )


@pytest.fixture()
def psql():
    """Salta la parte viva si el contenedor no está levantado."""
    try:
        resultado = _psql("SELECT 1;")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pytest.skip("docker no disponible")
    if resultado.returncode != 0:
        pytest.skip(f"PostgreSQL no responde: {resultado.stderr.strip()[:120]}")
    return _psql


class TestAplicacionViva:
    """Se ejecuta solo con el contenedor levantado. Lo omite si no hay conexión."""

    def test_el_esquema_se_puede_aplicar_dos_veces(self, psql) -> None:
        # La prueba de idempotencia: la segunda aplicación no debe fallar.
        primera = psql(SCHEMA.read_text(encoding="utf-8"))
        assert primera.returncode == 0, primera.stderr
        segunda = psql(SCHEMA.read_text(encoding="utf-8"))
        assert segunda.returncode == 0, segunda.stderr
        assert "ERROR" not in segunda.stderr

    def test_ai_predictions_rechaza_lo_que_el_srs_prohibe(self, psql) -> None:
        psql("DELETE FROM transactions WHERE broker_order_id = 'TEST-SCHEMA';")
        psql("DELETE FROM ai_predictions WHERE prediction_time = -1;")

        valida = psql(
            "INSERT INTO ai_predictions (prediction_time, trend_prediction, "
            "confidence_score, technical_rationale) VALUES (-1, 'STABLE', 0.5, 'x');"
        )
        assert valida.returncode == 0, valida.stderr

        for sql in (
            # Confianza fuera de [0,1]: RF-304 define una probabilidad.
            "INSERT INTO ai_predictions (prediction_time, trend_prediction, confidence_score, "
            "technical_rationale) VALUES (-1, 'UP', 1.5, 'x');",
            # Tendencia fuera del enumerado.
            "INSERT INTO ai_predictions (prediction_time, trend_prediction, confidence_score, "
            "technical_rationale) VALUES (-1, 'SIDEWAYS', 0.5, 'x');",
            # Justificación vacía: RF-303 exige explicabilidad para auditoría.
            "INSERT INTO ai_predictions (prediction_time, trend_prediction, confidence_score, "
            "technical_rationale) VALUES (-1, 'UP', 0.5, '   ');",
        ):
            rechazo = psql(sql)
            assert rechazo.returncode != 0, f"no debió aceptarse: {sql}"
            assert "ERROR" in rechazo.stderr

        # Y la limpieza deja la base como estaba.
        limpieza = psql("DELETE FROM ai_predictions WHERE prediction_time = -1;")
        assert limpieza.returncode == 0


class TestKillSwitchState:
    """RF-501 — el estado del Kill-Switch tiene que poder sobrevivir a un reinicio."""

    @staticmethod
    def _bloque() -> str:
        m = re.search(r"CREATE TABLE IF NOT EXISTS kill_switch_state\s*\((.*?)\n\);", SQL, re.S)
        assert m, "no se encontró la definición de kill_switch_state"
        return m.group(1)

    def test_tiene_las_columnas_del_estado(self) -> None:
        columnas = set(re.findall(r"^\s{4}(\w+)\s", self._bloque(), re.M)) - {"CONSTRAINT"}
        assert columnas == {
            "id", "activo", "motivo", "origen", "actor", "disparado_en",
            "liquidacion_estado", "liquidacion_en", "liquidaciones",
            "reinicio_en", "sesion_reinicio", "intentos_reinicio",
            "ultimo_intento_en",
        }

    def test_los_estados_y_origenes_estan_restringidos(self) -> None:
        bloque = self._bloque()
        assert "CHECK (origen IN ('MANUAL', 'AUTOMATICO'))" in bloque
        assert "CHECK (actor IN ('SISTEMA', 'OPERADOR'))" in bloque
        assert "CHECK (liquidacion_estado IN" in bloque
        for estado in ("NINGUNA", "PENDIENTE", "EN_CURSO", "COMPLETADA", "FALLIDA"):
            assert estado in bloque

    def test_el_esquema_no_siembra_la_fila(self) -> None:
        # La siembra es de Python (`sembrar_si_vacio`), igual que con
        # system_configurations: el DDL no fabrica datos.
        assert "kill_switch_state" not in re.findall(r"INSERT\s+INTO\s+(\w+)", SQL, flags=re.I)

    def test_no_hay_triggers_sobre_el_estado(self) -> None:
        # La inmutabilidad es de `audit_logs`; aquí el estado DEBE cambiar
        # (activar, liquidar, reiniciar), así que no lleva triggers.
        assert "kill_switch_state" not in re.findall(
            r"CREATE TRIGGER\s+\w+\s+BEFORE.*?ON\s+(\w+)", SQL, flags=re.I | re.S
        )


class TestAuditoriaInmutable:
    """RF-701 — la inmutabilidad se verifica contra el motor, no se supone.

    Lección de CR-003 (InfluxDB sobrescribía en silencio): el comportamiento
    de la base de datos se comprueba aquí, con el contenedor levantado, y se
    omite si no hay conexión.

    Cada prueba deja el INSERT de control dentro de la MISMA transacción que
    la sentencia que debe fallar. Si el rechazo llega, psql sale con
    ON_ERROR_STOP, la conexión se corta y PostgreSQL revuelve la transacción
    abierta: por eso al final no queda ningún marcador en la tabla y no hace
    falta un DELETE que, justamente, está prohibido.
    """

    @staticmethod
    def _insertar_y(sql: str) -> subprocess.CompletedProcess[str]:
        """INSERT de control + la sentencia que debe ser rechazada."""
        return _psql(
            "BEGIN;\n"
            "INSERT INTO audit_logs (timestamp, action, actor, ip_address, payload)\n"
            f"    VALUES (0, 'TEST', 'SISTEMA', '127.0.0.1', '{MARCA_AUDITORIA}');\n"
            f"{sql}\n"
        )

    @staticmethod
    def _marcadores(psql) -> int:
        """Filas de prueba que quedaron en la tabla (0 = no se coló ninguna)."""
        salida = psql(
            f"SELECT count(*) FROM audit_logs WHERE payload = '{MARCA_AUDITORIA}';",
            "-tA",
        )
        return int(salida.stdout.strip() or -1)

    def test_control_el_insert_si_funciona(self, psql) -> None:
        # Sin esto, un INSERT rechazado haría que todas las pruebas de abajo
        # "pasaran" por el motivo equivocado.
        control = _psql(
            "BEGIN;\n"
            "INSERT INTO audit_logs (timestamp, action, actor, ip_address, payload)\n"
            f"    VALUES (0, 'TEST', 'SISTEMA', '127.0.0.1', '{MARCA_AUDITORIA}');\n"
            "ROLLBACK;\n"
        )
        assert control.returncode == 0, control.stderr

    def test_update_se_rechaza(self, psql) -> None:
        rechazo = self._insertar_y(
            f"UPDATE audit_logs SET action = 'TAMPERED' WHERE payload = '{MARCA_AUDITORIA}';"
        )
        assert rechazo.returncode != 0, rechazo.stdout
        assert "inmutable" in rechazo.stderr
        assert "UPDATE" in rechazo.stderr
        assert self._marcadores(psql) == 0

    def test_delete_se_rechaza(self, psql) -> None:
        rechazo = self._insertar_y(
            f"DELETE FROM audit_logs WHERE payload = '{MARCA_AUDITORIA}';"
        )
        assert rechazo.returncode != 0, rechazo.stdout
        assert "inmutable" in rechazo.stderr
        assert "DELETE" in rechazo.stderr
        assert self._marcadores(psql) == 0

    def test_truncate_se_rechaza(self, psql) -> None:
        # El trigger por fila no se dispararía aquí: lo impide el de sentencia.
        rechazo = self._insertar_y("TRUNCATE audit_logs;")
        assert rechazo.returncode != 0, rechazo.stdout
        assert "inmutable" in rechazo.stderr
        assert self._marcadores(psql) == 0

    def test_los_triggers_estan_activos_y_en_always(self, psql) -> None:
        # `tgenabled = 'A'` es lo que significa ENABLE ALWAYS en el catálogo.
        salida = psql(
            "SELECT count(*) FROM pg_trigger\n"
            " WHERE tgrelid = 'audit_logs'::regclass\n"
            "   AND NOT tgisinternal AND tgenabled = 'A';",
            "-tA",
        )
        assert int(salida.stdout.strip()) == 2

    def test_un_rol_normal_no_puede_modificar(self, psql) -> None:
        # El REVOKE se verifica con un rol que NO es superusuario: el servicio
        # entra como superusuario y para ese los privilegios dan 't' aunque se
        # revocen — por eso, para él, la garantía real es el trigger (arriba).
        # El rol se crea y se revuelve en la misma transacción: no queda nada
        # detrás en el clúster.
        salida = psql(
            "BEGIN;\n"
            "DROP ROLE IF EXISTS ritech_prueba_auditoria;\n"
            "CREATE ROLE ritech_prueba_auditoria NOLOGIN;\n"
            "GRANT SELECT, INSERT ON audit_logs TO ritech_prueba_auditoria;\n"
            "SELECT has_table_privilege('ritech_prueba_auditoria', 'audit_logs', 'INSERT');\n"
            "SELECT has_table_privilege('ritech_prueba_auditoria', 'audit_logs', 'SELECT');\n"
            "SELECT has_table_privilege('ritech_prueba_auditoria', 'audit_logs', 'UPDATE');\n"
            "SELECT has_table_privilege('ritech_prueba_auditoria', 'audit_logs', 'DELETE');\n"
            "SELECT has_table_privilege('ritech_prueba_auditoria', 'audit_logs', 'TRUNCATE');\n"
            "ROLLBACK;\n",
            "-tA",
        )
        assert salida.returncode == 0, salida.stderr
        # Solo las respuestas de las cinco consultas (psql imprime además las
        # etiquetas de BEGIN/CREATE ROLE/GRANT/ROLLBACK).
        respuestas = [l for l in salida.stdout.splitlines() if l in ("t", "f")]
        assert respuestas == ["t", "t", "f", "f", "f"], respuestas

    def test_actor_es_obligatorio_y_esta_restringido(self, psql) -> None:
        sin_actor = psql(
            "INSERT INTO audit_logs (timestamp, action, ip_address) "
            "VALUES (0, 'TEST', '127.0.0.1');"
        )
        assert sin_actor.returncode != 0
        assert "actor" in sin_actor.stderr

        actor_malo = psql(
            "INSERT INTO audit_logs (timestamp, action, actor, ip_address) "
            "VALUES (0, 'TEST', 'HACKER', '127.0.0.1');"
        )
        assert actor_malo.returncode != 0
        assert "audit_logs_actor_valido" in actor_malo.stderr

    def test_las_filas_previas_quedaron_con_actor_sin_default(self, psql) -> None:
        # La migración del bloque DO: backfill + NOT NULL + sin default.
        nulos = psql("SELECT count(*) FROM audit_logs WHERE actor IS NULL;", "-tA")
        assert int(nulos.stdout.strip()) == 0

        default = psql(
            "SELECT coalesce(column_default, '') FROM information_schema.columns\n"
            " WHERE table_schema = 'public' AND table_name = 'audit_logs'\n"
            "   AND column_name = 'actor';",
            "-tA",
        )
        assert default.stdout.strip() == ""

    def test_no_quedaron_marcadores_de_las_pruebas(self, psql) -> None:
        # Barrido final: ninguna prueba dejó filas detrás.
        assert self._marcadores(psql) == 0
