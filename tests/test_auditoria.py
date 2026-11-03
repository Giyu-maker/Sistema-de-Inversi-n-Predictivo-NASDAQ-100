"""Auditoría: la validación del evento, el SQL que se manda y la regla estricta.

Sin contenedor: la conexión se sustituye por un doble que graba las
sentencias. Lo que se verifica aquí es lo que **no** depende de PostgreSQL —
que el evento se valide antes de tocar la base, que el INSERT lleve los seis
campos en el orden declarado, que el reloj se respete y que
`auditar_sincrono` **no** trague un fallo—.

La inmutabilidad de verdad (triggers de UPDATE/DELETE/TRUNCATE, privilegios
revocados, migración de `actor`) se prueba contra el motor real en
`tests/test_schema.py::TestAuditoriaInmutable`, porque esa garantía es de
PostgreSQL y no de este módulo.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import pytest

from src.database import pg
from src.security.auditoria import (
    ACTORES_VALIDOS,
    AuditoriaPostgres,
    ErrorDeAuditoria,
    EventoAuditoria,
    auditar_sincrono,
)

EVENTO = EventoAuditoria(
    action="KILL_SWITCH_TRIGGERED",
    actor="OPERADOR",
    ip_address="10.0.0.7",
    payload={"motivo": "pulsación manual"},
)


class _Cursor:
    """Cursor que solo graba: no devuelve filas salvo que se le pidan."""

    def __init__(self, error: Exception | None = None) -> None:
        self.ejecuciones: list[tuple[str, Any]] = []
        self._error = error

    def execute(self, sql: str, params: Any = None) -> None:
        if self._error is not None:
            raise self._error
        self.ejecuciones.append((sql, params))

    def fetchone(self) -> tuple | None:
        return None

    def __enter__(self) -> "_Cursor":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class _Conexion:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0
        self.cerrada = False

    def cursor(self) -> _Cursor:
        return self._cursor

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.cerrada = True


@pytest.fixture
def conexion_falsa(monkeypatch: pytest.MonkeyPatch):
    """Instala una `pg.conexion` que devuelve el doble pedido (patrón de test_repos)."""

    def _instalar(cursor: _Cursor) -> _Cursor:
        conexion = _Conexion(cursor)

        @contextmanager
        def _falsa(dsn: str | None = None) -> Iterator[_Conexion]:
            del dsn
            try:
                yield conexion
                conexion.commit()
            except Exception:
                conexion.rollback()
                raise
            finally:
                conexion.close()

        monkeypatch.setattr(pg, "conexion", _falsa)
        _instalar.conexion = conexion  # type: ignore[attr-defined]
        return cursor

    return _instalar


class TestEvento:
    def test_acepta_los_tres_actores_del_esquema(self) -> None:
        for actor in ACTORES_VALIDOS:
            evento = EventoAuditoria(action="X", actor=actor, ip_address="127.0.0.1")
            assert evento.actor == actor

    def test_rechaza_un_actor_fuera_de_la_lista(self) -> None:
        # El CHECK del esquema haría lo mismo, pero desde el caller y sin
        # gastar una transacción en averiguarlo.
        with pytest.raises(ValueError, match="actor desconocido"):
            EventoAuditoria(action="X", actor="ADMIN", ip_address="127.0.0.1")

    @pytest.mark.parametrize("action", ["", "   "])
    def test_rechaza_accion_vacia(self, action: str) -> None:
        with pytest.raises(ValueError, match="acción"):
            EventoAuditoria(action=action, actor="SISTEMA", ip_address="127.0.0.1")

    def test_rechaza_accion_mas_larga_que_la_columna(self) -> None:
        # VARCHAR(100): mejor fallar aquí que con un error de longitud opaco.
        with pytest.raises(ValueError, match="100 caracteres"):
            EventoAuditoria(action="A" * 101, actor="SISTEMA", ip_address="127.0.0.1")

    def test_la_ip_es_obligatoria(self) -> None:
        # AGENTS.md §5.3: sin IP el registro no prueba desde dónde se actuó.
        with pytest.raises(ValueError, match="IP"):
            EventoAuditoria(action="X", actor="SISTEMA", ip_address="  ")

    def test_rechaza_un_payload_con_referencia_circular(self) -> None:
        # `default=str` salva a los tipos exóticos, pero un payload mal formado
        # no debería llegar a la base: mejor fallar en el caller.
        circular: dict[str, Any] = {}
        circular["yo"] = circular
        with pytest.raises(ValueError, match="Circular"):
            EventoAuditoria(
                action="X", actor="SISTEMA", ip_address="127.0.0.1",
                payload=circular,
            )

    def test_rechaza_un_payload_con_claves_que_no_son_texto(self) -> None:
        with pytest.raises(TypeError):
            EventoAuditoria(
                action="X", actor="SISTEMA", ip_address="127.0.0.1",
                payload={(1, 2): "valor"},
            )

    def test_los_tipos_exoticos_se_registran_en_lugar_de_romper_la_auditoria(self) -> None:
        # Un Decimal de psycopg2 o un datetime no pueden tumbar el log: la
        # auditoría se convierte en texto y sigue.
        from datetime import datetime
        from decimal import Decimal

        evento = EventoAuditoria(
            action="X", actor="SISTEMA", ip_address="127.0.0.1",
            payload={"precio": Decimal("10.5"), "cuando": datetime(2026, 11, 3, 9, 30)},
        )
        texto = evento.payload_json()
        assert texto is not None and "10.5" in texto and "2026-11-03" in texto

    def test_payload_vacio_queda_en_none(self) -> None:
        assert EventoAuditoria(action="X", actor="SISTEMA", ip_address="127.0.0.1").payload_json() is None

    def test_payload_es_json_legible_sin_escapar_tildes(self) -> None:
        evento = EventoAuditoria(
            action="X", actor="SISTEMA", ip_address="127.0.0.1",
            payload={"motivo": "reinicio", "n": 2},
        )
        assert evento.payload_json() == '{"motivo": "reinicio", "n": 2}'


class TestRegistro:
    def test_el_insert_lleva_los_seis_campos(self, conexion_falsa) -> None:
        cursor = _Cursor()
        conexion_falsa(cursor)

        AuditoriaPostgres().registrar(EVENTO)

        sql, params = cursor.ejecuciones[0]
        for columna in ("timestamp", "user_id", "action", "actor", "ip_address", "payload"):
            assert columna in sql, f"falta {columna} en el INSERT"
        timestamp, user_id, action, actor, ip, payload = params
        assert isinstance(timestamp, int) and timestamp > 1_700_000_000_000
        assert (user_id, action, actor, ip, payload) == (
            None, "KILL_SWITCH_TRIGGERED", "OPERADOR", "10.0.0.7",
            '{"motivo": "pulsación manual"}',
        )
        assert conexion_falsa.conexion.commits == 1  # type: ignore[attr-defined]
        assert conexion_falsa.conexion.cerrada  # type: ignore[attr-defined]

    def test_usa_el_reloj_inyectado_cuando_no_hay_timestamp(self, conexion_falsa) -> None:
        cursor = _Cursor()
        conexion_falsa(cursor)

        AuditoriaPostgres(ahora=lambda: 1_800_000_000.5).registrar(
            EventoAuditoria(action="X", actor="SISTEMA", ip_address="127.0.0.1")
        )

        assert cursor.ejecuciones[0][1][0] == 1_800_000_000_500

    def test_respeta_un_timestamp_explicito(self, conexion_falsa) -> None:
        cursor = _Cursor()
        conexion_falsa(cursor)

        AuditoriaPostgres(ahora=lambda: 0.0).registrar(
            EventoAuditoria(
                action="X", actor="SISTEMA", ip_address="127.0.0.1",
                timestamp_ms=42,
            )
        )

        assert cursor.ejecuciones[0][1][0] == 42

    def test_un_fallo_de_base_de_datos_se_traduce_sin_taparse(self, conexion_falsa) -> None:
        # La regla 3 exige que el error llegue arriba: se envuelve para que el
        # caller sepa que falló la auditoría, no su propia lógica.
        conexion_falsa(_Cursor(error=RuntimeError("conexion rechazada")))

        with pytest.raises(ErrorDeAuditoria, match="KILL_SWITCH_TRIGGERED") as fallo:
            AuditoriaPostgres().registrar(EVENTO)

        assert "conexion rechazada" in str(fallo.value)


class TestReglaEstricta:
    def test_propaga_el_fallo_de_auditoria(self) -> None:
        class _Roto:
            def registrar(self, evento: EventoAuditoria) -> None:
                raise ErrorDeAuditoria("sin auditoría")

        with pytest.raises(ErrorDeAuditoria, match="sin auditoría"):
            auditar_sincrono(_Roto(), EVENTO)

    def test_deja_pasar_un_registro_exitoso(self) -> None:
        class _Bueno:
            def __init__(self) -> None:
                self.eventos: list[EventoAuditoria] = []

            def registrar(self, evento: EventoAuditoria) -> None:
                self.eventos.append(evento)

        bueno = _Bueno()
        auditar_sincrono(bueno, EVENTO)
        assert bueno.eventos == [EVENTO]
