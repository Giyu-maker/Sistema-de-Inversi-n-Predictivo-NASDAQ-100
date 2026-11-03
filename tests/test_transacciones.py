"""`transactions` — el SQL que se manda y los espejos del esquema (RF-602).

Sin contenedor: la conexión se sustituye por un doble. Lo que se verifica es
que el INSERT lleve los siete campos, que `is_simulated` se escriba como TRUE
y que las validaciones repliquen los CHECK de `src/database/schema.sql` antes
de tocar la base.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import pytest

from src.database import pg
from src.database.transacciones import (
    TransaccionNueva,
    TransaccionesPostgres,
    _validar,
)

UUID_PREDICCION = "3f1e0a4c-77f0-4d6e-9a1b-2c3d4e5f6071"

VALIDA = TransaccionNueva(
    broker_order_id="SIM-1",
    transaction_type="BUY",
    quantity=2.0,
    execution_price=512.5,
    execution_time=1_800_000_000_000,
    prediction_id=UUID_PREDICCION,
)


class _Cursor:
    def __init__(self, filas: list[tuple] | None = None, conteo: tuple | None = None) -> None:
        self.filas = list(filas or [])
        self._conteo = conteo
        self.ejecuciones: list[tuple[str, Any]] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.ejecuciones.append((sql, params))

    def fetchone(self) -> tuple | None:
        if self.filas:
            return self.filas.pop(0)
        if self._conteo is not None:
            # Como en psycopg2: el segundo resultado reemplaza al primero, y
            # el doble debe comportarse igual (mismo patrón que test_repos).
            self._conteo, fila = None, self._conteo
            return fila
        return None

    def fetchall(self) -> list[tuple]:
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
        self.cerrada = False

    def cursor(self) -> _Cursor:
        return self._cursor

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        self.cerrada = True


@pytest.fixture
def conexion_falsa(monkeypatch: pytest.MonkeyPatch):
    def _instalar(cursor: _Cursor) -> _Cursor:
        conexion = _Conexion(cursor)

        @contextmanager
        def _falsa(dsn: str | None = None) -> Iterator[_Conexion]:
            del dsn
            try:
                yield conexion
                conexion.commit()
            finally:
                conexion.close()

        monkeypatch.setattr(pg, "conexion", _falsa)
        return cursor

    return _instalar


class TestValidacion:
    def test_el_simulado_es_true_por_defecto(self) -> None:
        # RF-601: en esta fase nada puede salir como orden real por accidente.
        assert TransaccionNueva(
            broker_order_id="SIM-1", transaction_type="BUY", quantity=1.0,
            execution_price=1.0, execution_time=0,
        ).is_simulated is True

    @pytest.mark.parametrize("tipo", ["HOLD", "venta", ""])
    def test_tipo_invalido(self, tipo: str) -> None:
        with pytest.raises(ValueError, match="tipo"):
            _validar(TransaccionNueva(
                broker_order_id="SIM-1", transaction_type=tipo, quantity=1.0,  # type: ignore[arg-type]
                execution_price=1.0, execution_time=0,
            ))

    @pytest.mark.parametrize("cantidad", [0, -1.5])
    def test_cantidad_no_positiva(self, cantidad: float) -> None:
        with pytest.raises(ValueError, match="cantidad"):
            _validar(TransaccionNueva(
                broker_order_id="SIM-1", transaction_type="BUY", quantity=cantidad,
                execution_price=1.0, execution_time=0,
            ))

    def test_precio_negativo(self) -> None:
        with pytest.raises(ValueError, match="precio"):
            _validar(TransaccionNueva(
                broker_order_id="SIM-1", transaction_type="BUY", quantity=1.0,
                execution_price=-1.0, execution_time=0,
            ))

    @pytest.mark.parametrize("orden", ["", "   "])
    def test_orden_sin_identificador(self, orden: str) -> None:
        with pytest.raises(ValueError, match="identificador"):
            _validar(TransaccionNueva(
                broker_order_id=orden, transaction_type="BUY", quantity=1.0,
                execution_price=1.0, execution_time=0,
            ))

    def test_prediction_id_que_no_es_uuid(self) -> None:
        # Es una FK: validarla aquí evita un error de tipo opaco de psycopg2.
        with pytest.raises(ValueError, match="UUID"):
            _validar(TransaccionNueva(
                broker_order_id="SIM-1", transaction_type="BUY", quantity=1.0,
                execution_price=1.0, execution_time=0, prediction_id="pred-1",
            ))

    def test_prediction_id_puede_ser_nulo(self) -> None:
        # La liquidación no nace de una predicción: la FK es NULLABLE (SDD §3.1.1).
        _validar(TransaccionNueva(
            broker_order_id="SIM-1", transaction_type="SELL", quantity=1.0,
            execution_price=1.0, execution_time=0, prediction_id=None,
        ))

    def test_valida_antes_de_tocar_la_base(self, conexion_falsa) -> None:
        cursor = _Cursor()
        conexion_falsa(cursor)
        with pytest.raises(ValueError):
            _validar(TransaccionNueva(
                broker_order_id="SIM-1", transaction_type="BUY", quantity=0.0,
                execution_price=1.0, execution_time=0,
            ))
        assert cursor.ejecuciones == []


class TestSql:
    def test_el_insert_lleva_los_siete_campos(self, conexion_falsa) -> None:
        cursor = _Cursor(filas=[("id-1",)])
        conexion_falsa(cursor)

        identificador = TransaccionesPostgres().guardar(VALIDA)

        sql, params = cursor.ejecuciones[0]
        for columna in ("prediction_id", "broker_order_id", "transaction_type",
                        "quantity", "execution_price", "execution_time", "is_simulated"):
            assert columna in sql, f"falta {columna}"
        assert params == (
            UUID_PREDICCION, "SIM-1", "BUY", 2.0, 512.5, 1_800_000_000_000, True,
        )
        assert identificador == "id-1"

    def test_el_neto_suma_compras_menos_ventas(self, conexion_falsa) -> None:
        cursor = _Cursor(filas=[(3.0,)])
        conexion_falsa(cursor)

        assert TransaccionesPostgres().cantidad_neta() == 3.0

        sql = cursor.ejecuciones[0][0]
        assert "transaction_type = 'BUY'" in sql
        assert "ELSE -quantity" in sql

    def test_listar_devuelve_la_pagina_y_el_total(self, conexion_falsa) -> None:
        cursor = _Cursor(
            filas=[("id-1", None, "SIM-1", "BUY", 2.0, 512.5, 1_800_000_000_000, True)],
            conteo=(7,),
        )
        conexion_falsa(cursor)

        filas, total = TransaccionesPostgres().listar(20, 0)

        assert total == 7
        assert len(filas) == 1
        assert filas[0].broker_order_id == "SIM-1"
        assert filas[0].prediction_id is None
        assert filas[0].is_simulated is True
        assert filas[0].quantity == 2.0

    def test_listar_es_descendente_por_tiempo(self, conexion_falsa) -> None:
        cursor = _Cursor(conteo=(0,))
        conexion_falsa(cursor)
        TransaccionesPostgres().listar(10, 0)
        assert "ORDER BY execution_time DESC" in cursor.ejecuciones[0][0]
