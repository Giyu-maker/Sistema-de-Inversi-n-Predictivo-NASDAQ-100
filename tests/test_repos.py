"""Capa de PostgreSQL — el SQL que se manda y el DSN que se arma.

Sin contenedor: la conexión se sustituye por un doble que graba las
sentencias. Eso permite verificar lo que de verdad importa de esta capa —
el `ON CONFLICT` de insert-if-absent, el mapeo de filas y que nada se escribe
sin querer — sin necesitar una base de datos viva para cada test.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import pytest

from src.database import pg
from src.database.configuraciones import (
    ConfiguracionesPostgres,
    configuracion_por_defecto,
)
from src.database.predictions import PrediccionNueva, PrediccionesPostgres, _validar
from src.security.perfiles import UMBRALES_POR_PERFIL

NUEVA = PrediccionNueva(
    prediction_time=1_800_000_000_000,
    trend_prediction="UP",
    confidence_score=0.8123,
    technical_rationale="Cruce de medias con volumen creciente.",
)


class _Cursor:
    def __init__(self, filas: list[tuple] | None = None, rowcount: int = 0,
                 conteo: tuple | None = None) -> None:
        self.filas = list(filas or [])
        self.rowcount = rowcount
        self._conteo = conteo
        self.ejecuciones: list[tuple[str, Any]] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.ejecuciones.append((sql, params))

    def fetchone(self) -> tuple | None:
        if self.filas:
            return self.filas.pop(0)
        if self._conteo is not None:
            # Fila del `count(*)`: en psycopg2 el segundo resultado reemplaza
            # al primero, y el doble debe comportarse igual.
            self._conteo, fila = None, self._conteo
            return fila
        return None

    def fetchall(self) -> list[tuple]:
        # Como en psycopg2: `fetchall` consume el resultado, que es lo que
        # permite hacer `fetchall` y luego otro `execute` en la misma sesión.
        filas, self.filas = self.filas, []
        return filas

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
    """Sustituye `pg.conexion` por una que devuelve el doble pedido.

    Devuelve el **cursor** (lo que casi todos los tests quieren mirar) y deja la
    conexión en `conexion_falsa.conexion` para quien necesite ver commits.
    """

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


class TestPrediccionesSql:
    def test_guardar_es_insert_if_absent_por_prediction_time(self, conexion_falsa) -> None:
        cursor = _Cursor(filas=[("id-fila",)])
        conexion_falsa(cursor)

        guardada = PrediccionesPostgres().guardar(NUEVA)

        sql, params = cursor.ejecuciones[0]
        assert "ON CONFLICT (prediction_time) DO NOTHING" in sql
        assert "RETURNING id" in sql
        assert params == (
            NUEVA.prediction_time, "UP", 0.8123, NUEVA.technical_rationale
        )
        assert guardada is not None
        assert guardada.id == "id-fila"
        assert guardada.prediction_time == NUEVA.prediction_time
        assert conexion_falsa.conexion.commits == 1  # type: ignore[attr-defined]
        assert conexion_falsa.conexion.cerrada  # type: ignore[attr-defined]

    def test_guardar_devuelve_none_cuando_ya_existia(self, conexion_falsa) -> None:
        # El conflicto no lanza: devuelve None y no escribe una segunda fila.
        cursor = _Cursor(filas=[])
        conexion_falsa(cursor)
        assert PrediccionesPostgres().guardar(NUEVA) is None

    def test_guardar_valida_antes_de_tocar_la_base(self, conexion_falsa) -> None:
        cursor = _Cursor()
        conexion_falsa(cursor)
        mala = PrediccionNueva(
            prediction_time=1, trend_prediction="SUBE",
            confidence_score=0.5, technical_rationale="x",
        )
        with pytest.raises(ValueError, match="tendencia inválida"):
            PrediccionesPostgres().guardar(mala)
        assert cursor.ejecuciones == []  # ni una sentencia

    @pytest.mark.parametrize(
        "mala,motivo",
        [
            (PrediccionNueva(1, "UP", 1.5, "x"), "confianza"),
            (PrediccionNueva(1, "UP", -0.1, "x"), "confianza"),
            (PrediccionNueva(1, "DOWN", 0.5, "   "), "vacía"),
        ],
    )
    def test_los_check_del_esquema_tambien_estan_en_python(self, mala, motivo) -> None:
        with pytest.raises(ValueError, match=motivo):
            _validar(mala)

    def test_ultima_mapea_la_fila_mas_reciente(self, conexion_falsa) -> None:
        cursor = _Cursor(filas=[("id-9", 42, "STABLE", 0.9999, "Sin movimiento.")])
        conexion_falsa(cursor)

        ultima = PrediccionesPostgres().ultima()

        assert ultima is not None
        assert ultima.id == "id-9"
        assert ultima.prediction_time == 42
        # DECIMAL de PostgreSQL llega como Decimal: se expone como float.
        assert ultima.confidence_score == pytest.approx(0.9999)
        assert "ORDER BY prediction_time DESC" in cursor.ejecuciones[0][0]

    def test_ultima_sin_filas_es_none(self, conexion_falsa) -> None:
        conexion_falsa(_Cursor(filas=[]))
        assert PrediccionesPostgres().ultima() is None

    def test_listar_devuelve_pagina_y_total(self, conexion_falsa) -> None:
        cursor = _Cursor(
            filas=[("a", 30, "UP", 0.8, "r"), ("b", 20, "DOWN", 0.7, "r")],
            conteo=(2,),
        )
        conexion_falsa(cursor)

        pagina, total = PrediccionesPostgres().listar(limit=2, offset=0)

        assert [f.id for f in pagina] == ["a", "b"]
        assert total == 2
        assert "LIMIT %s OFFSET %s" in cursor.ejecuciones[0][0]
        assert cursor.ejecuciones[1][0].strip().startswith("SELECT count(*)")


class TestConfiguracionesSql:
    def test_sembrar_inicial_solo_inserta_si_la_tabla_esta_vacia(self, conexion_falsa) -> None:
        cursor = _Cursor()
        cursor.rowcount = 1  # «1 fila insertada»
        conexion_falsa(cursor)

        assert ConfiguracionesPostgres().sembrar_inicial() is True

        sql, params = cursor.ejecuciones[0]
        # Ni un solo INSERT a ciegas: la tabla se consulta en la propia
        # sentencia, así que dos procesos a la vez no pueden sembrar dos filas.
        assert "WHERE NOT EXISTS (SELECT 1 FROM system_configurations)" in sql
        # D3: MODERATE (0,75), no el DEFAULT 'CONVENTIONAL' de la columna.
        assert params == (1.25, False, "MODERATE")
        assert conexion_falsa.conexion.commits == 1  # type: ignore[attr-defined]

    def test_sembrar_inicial_es_no_si_ya_habia_fila(self, conexion_falsa) -> None:
        cursor = _Cursor()
        cursor.rowcount = 0
        conexion_falsa(cursor)
        assert ConfiguracionesPostgres().sembrar_inicial() is False

    def test_leer_sin_filas_es_none(self, conexion_falsa) -> None:
        conexion_falsa(_Cursor(filas=[]))
        assert ConfiguracionesPostgres().leer() is None

    def test_leer_deriva_el_umbral_del_perfil(self, conexion_falsa) -> None:
        conexion_falsa(_Cursor(filas=[(1.25, False, "CONVENTIONAL")]))
        config = ConfiguracionesPostgres().leer()
        assert config is not None
        assert config.risk_profile == "CONVENTIONAL"
        assert config.umbral_confianza == UMBRALES_POR_PERFIL["CONVENTIONAL"]

    def test_valores_por_defecto_son_los_del_rf304(self) -> None:
        config = configuracion_por_defecto()
        assert config.max_loss_day == 1.25
        assert config.is_autonomous is False
        assert config.risk_profile == "MODERATE"
        assert config.umbral_confianza == 0.75


class TestDsn:
    def test_se_arma_con_postgres_y_pg_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in ("PGDSN", "PGPASSWORD"):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setenv("PGHOST", "10.0.0.2")
        monkeypatch.setenv("PGPORT", "5433")
        monkeypatch.setenv("POSTGRES_DB", "ritech")
        monkeypatch.setenv("POSTGRES_USER", "ritech")
        monkeypatch.setenv("POSTGRES_PASSWORD", "secreta")

        assert pg.dsn() == (
            "host=10.0.0.2 port=5433 dbname=ritech user=ritech password=secreta"
        )

    def test_pgdse_explicita_gana(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PGDSN", "host=otro dbname=x")
        assert pg.dsn() == "host=otro dbname=x"

    def test_sin_configuracion_no_se_adivina_nada(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in ("PGDSN", "POSTGRES_DB", "POSTGRES_USER"):
            monkeypatch.delenv(var, raising=False)
        with pytest.raises(pg.ErrorDeBaseDatos, match="POSTGRES_DB"):
            pg.dsn()
