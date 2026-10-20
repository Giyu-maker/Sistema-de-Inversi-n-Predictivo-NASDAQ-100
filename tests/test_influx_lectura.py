"""Lectura de InfluxDB — los scripts Flux que se mandan (SDD §6.2).

La agregación vive en base de datos, así que lo que hay que probar aquí es el
script, no un cálculo en Python: si el `union` por campo o el `pivot`
salieran mal, la API devolvería precios agregados con la función equivocada y
nadie lo vería en el gráfico hasta que ya estaba operando.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.database.influx_lectura import (
    CAMPOS,
    MEASUREMENT,
    _a_vela,
    flux_rango,
    flux_ultimas,
)

INICIO = int(datetime(2026, 10, 16, 13, 30, tzinfo=timezone.utc).timestamp() * 1000)
FIN = INICIO + 60 * 60 * 1000


class _Registro:
    """Lo mínimo que usa `_a_vela`: `.values` y `get_time()`."""

    def __init__(self, ts: datetime, valores: dict) -> None:
        self._ts = ts
        self.values = valores

    def get_time(self) -> datetime:
        return self._ts


def _valores(precio: float = 500.0) -> dict:
    return {
        "price_open": precio,
        "price_high": precio + 1,
        "price_low": precio - 1,
        "price_close": precio,
        "volume": 1000.0,
    }


class TestFluxUltimas:
    def test_trae_las_ultimas_n_y_las_deja_ordenadas(self) -> None:
        script = flux_ultimas("nasdaq_ticks", 400)
        assert 'from(bucket: "nasdaq_ticks")' in script
        assert "range(start: 0)" in script
        assert f'r._measurement == "{MEASUREMENT}"' in script
        for campo in CAMPOS:
            assert f'r._field == "{campo}"' in script
        assert "limit(n: 400)" in script
        # Orden ascendente al final: el gráfico y la ventana lo necesitan.
        assert script.count('sort(columns: ["_time"])') == 1
        assert 'sort(columns: ["_time"], desc: true)' in script

    def test_el_pivot_convierte_una_serie_por_campo_en_filas_ohlc(self) -> None:
        script = flux_ultimas("nasdaq_ticks", 10)
        assert 'pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")' in script

    def test_n_menos_de_uno_es_error(self) -> None:
        with pytest.raises(ValueError, match="al menos 1"):
            flux_ultimas("nasdaq_ticks", 0)


class TestFluxRango:
    def test_rango_sin_agregar_no_usa_aggregate_window(self) -> None:
        script = flux_rango("nasdaq_ticks", INICIO, FIN, "5m")
        assert "aggregateWindow" not in script
        assert "union(tables:" not in script
        assert f'time(v: "{_rfc3339(INICIO)}")' in script
        assert f'stop: time(v: "{_rfc3339(FIN)}")' in script

    @pytest.mark.parametrize("agregacion", ["15m", "1h", "1d"])
    def test_agregacion_por_columna_no_global(self, agregacion: str) -> None:
        script = flux_rango("nasdaq_ticks", INICIO, FIN, agregacion)
        # Un solo aggregateWindow con una función para todas las columnas daría
        # la suma de aperturas, que no significa nada: una función por columna.
        assert script.count("aggregateWindow") == len(CAMPOS)
        for funcion in ("first", "max", "min", "last", "sum"):
            assert f"fn: {funcion}" in script
        assert script.count("union(tables:") == 1
        assert f"every: {agregacion}" in script

    def test_con_limit_se_acota_despues_de_agregar(self) -> None:
        script = flux_rango("nasdaq_ticks", INICIO, FIN, "1h", limit=50)
        assert script.rstrip().endswith("limit(n: 50)")
        # El límite va DESPUÉS de la agregación: 50 velas de 1h, no 50 minutos.
        assert script.index("aggregateWindow") < script.index("limit(n: 50)")

    def test_agregacion_desconocida_es_error(self) -> None:
        with pytest.raises(ValueError, match="agregación no soportada"):
            flux_rango("nasdaq_ticks", INICIO, FIN, "7m")

    def test_rango_invertido_es_error(self) -> None:
        with pytest.raises(ValueError, match="anterior al fin"):
            flux_rango("nasdaq_ticks", FIN, INICIO, "5m")

    def test_limit_cero_es_error(self) -> None:
        with pytest.raises(ValueError, match="al menos 1"):
            flux_rango("nasdaq_ticks", INICIO, FIN, "5m", limit=0)


class TestBucket:
    @pytest.mark.parametrize("bucket", ['mal"quote', "con espacios", "con\ttab", ""])
    def test_bucket_con_comillas_o_espacios_es_error(self, bucket: str) -> None:
        # El viaja dentro del script: sin caracteres que lo puedan romper.
        with pytest.raises(ValueError, match="bucket"):
            flux_ultimas(bucket, 10)

    def test_bucket_normal_pasa_tal_cual(self) -> None:
        assert 'from(bucket: "nasdaq_ticks")' in flux_ultimas("nasdaq_ticks", 5)


class TestAVela:
    def test_convierte_un_registro_pivotado(self) -> None:
        ts = datetime(2026, 10, 16, 14, 5, tzinfo=timezone.utc)
        vela = _a_vela(_Registro(ts, _valores(512.5)))
        assert vela is not None
        assert vela.timestamp == int(ts.timestamp() * 1000)
        assert vela.price_open == 512.5
        assert vela.price_high == 513.5
        assert vela.price_low == 511.5
        assert vela.price_close == 512.5
        assert vela.volume == 1000.0

    def test_acepta_enteros_y_los_normaliza_a_float(self) -> None:
        valores = _valores()
        valores["volume"] = 1000  # Influx puede devolver int
        vela = _a_vela(_Registro(datetime.now(timezone.utc), valores))
        assert vela is not None and isinstance(vela.volume, float)

    @pytest.mark.parametrize("faltante", CAMPOS)
    def test_un_campo_faltante_descarta_la_vela_en_lugar_de_poner_cero(self, faltante: str) -> None:
        # Meter un 0 fabricaría un gráfico que miente sobre el mercado.
        valores = _valores()
        valores[faltante] = None
        assert _a_vela(_Registro(datetime.now(timezone.utc), valores)) is None


def _rfc3339(ms: int) -> str:
    return (
        datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
