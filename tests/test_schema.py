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
    "trader_users", "system_configurations", "ai_predictions", "transactions", "audit_logs",
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
        sin_guardia = re.findall(r"CREATE INDEX\s+(?!IF NOT EXISTS)(\w+)", SQL)
        assert sin_guardia == [], f"índices sin IF NOT EXISTS: {sin_guardia}"
        assert len(re.findall(r"CREATE INDEX IF NOT EXISTS", SQL)) == 5

    def test_no_hay_sentencias_destructivas(self) -> None:
        # DROP/TRUNCATE/ALTER ... CASCADE vaciarían la auditoría de 5 años.
        destructivas = re.findall(
            r"\b(DROP|TRUNCATE|ALTER\s+TABLE\s+\w+\s+COLUMN)\b", SQL, flags=re.I
        )
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
        for indice in ("idx_ai_predictions_time", "idx_transactions_time",
                       "idx_audit_logs_time", "idx_audit_logs_action"):
            assert f"CREATE INDEX IF NOT EXISTS {indice}" in SQL


class TestAplicacionViva:
    """Se ejecuta solo con el contenedor levantado. Lo omite si no hay conexión."""

    @staticmethod
    def _psql(sql: str) -> subprocess.CompletedProcess[str]:
        entorno = os.environ.copy()
        usuario = entorno.get("POSTGRES_USER", "ritech")
        base = entorno.get("POSTGRES_DB", "ritech")
        return subprocess.run(
            ["docker", "exec", "-i", "ritech-postgres16", "psql",
             "-U", usuario, "-d", base, "-v", "ON_ERROR_STOP=1"],
            input=sql, capture_output=True, text=True, timeout=60,
        )

    @pytest.fixture()
    def psql(self):
        try:
            resultado = self._psql("SELECT 1;")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pytest.skip("docker no disponible")
        if resultado.returncode != 0:
            pytest.skip(f"PostgreSQL no responde: {resultado.stderr.strip()[:120]}")
        return self._psql

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
