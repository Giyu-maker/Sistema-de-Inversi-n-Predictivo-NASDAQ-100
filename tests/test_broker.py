"""`SimulatedBrokerGateway` — Modo Sombra sin tocar capital (RF-601, RF-602).

Con el repositorio falsificado: se verifica la política del gateway (todo se
registra como simulado, el precio se pide antes de escribir, sin posición no
hay orden) y no el SQL, que vive en `tests/test_transacciones.py`.
"""

from __future__ import annotations

import pytest

from src.database.transacciones import TransaccionNueva, TransaccionGuardada
from src.integration.broker import NuevaOrden, SimulatedBrokerGateway
from tests.fakes import TransaccionesFalsas

UUID_PREDICCION = "3f1e0a4c-77f0-4d6e-9a1b-2c3d4e5f6071"


def gateway(
    repo: TransaccionesFalsas | None = None,
    *,
    precio: float = 512.5,
    falla_precio: Exception | None = None,
    simbolo: str = "QQQ",
) -> tuple[SimulatedBrokerGateway, TransaccionesFalsas]:
    repo = repo if repo is not None else TransaccionesFalsas()

    def _precio(_simbolo: str) -> float:
        if falla_precio is not None:
            raise falla_precio
        return precio

    return (
        SimulatedBrokerGateway(
            repo,
            precio_actual=_precio,
            simbolo=simbolo,
            ahora_ms=lambda: 1_800_000_000_000,
            nuevo_id=lambda: "SIM-TEST-1",
        ),
        repo,
    )


class TestOrdenSimulada:
    def test_todo_se_registra_como_simulado(self) -> None:
        gw, repo = gateway()
        orden = gw.enviar_orden(
            NuevaOrden(tipo="BUY", cantidad=2, prediction_id=UUID_PREDICCION)
        )

        assert orden.is_simulated is True
        assert repo.guardadas[0].is_simulated is True  # RF-602
        assert repo.guardadas[0].transaction_type == "BUY"
        assert repo.guardadas[0].prediction_id == UUID_PREDICCION  # trazabilidad
        assert orden.broker_order_id == "SIM-TEST-1"

    def test_el_precio_sale_de_la_fuente_inyectada(self) -> None:
        gw, repo = gateway(precio=480.0)
        gw.enviar_orden(NuevaOrden(tipo="BUY", cantidad=1))
        assert repo.guardadas[0].execution_price == 480.0

    def test_se_pide_el_precio_antes_de_escribir(self) -> None:
        # Si la fuente de precios no responde, no queda una orden con precio
        # inventado: ni una fila.
        gw, repo = gateway(falla_precio=RuntimeError("influxdb caído"))
        with pytest.raises(RuntimeError, match="influxdb"):
            gw.enviar_orden(NuevaOrden(tipo="BUY", cantidad=1))
        assert repo.guardadas == []

    def test_un_precio_no_positivo_no_se_acepta(self) -> None:
        gw, repo = gateway(precio=0.0)
        with pytest.raises(ValueError, match="precio"):
            gw.enviar_orden(NuevaOrden(tipo="BUY", cantidad=1))
        assert repo.guardadas == []

    @pytest.mark.parametrize("cantidad", [0, -3])
    def test_cantidad_no_positiva_no_se_acepta(self, cantidad: float) -> None:
        gw, repo = gateway()
        with pytest.raises(ValueError, match="cantidad"):
            gw.enviar_orden(NuevaOrden(tipo="BUY", cantidad=cantidad))
        assert repo.guardadas == []

    def test_un_tipo_invalido_no_se_acepta(self) -> None:
        gw, repo = gateway()
        with pytest.raises(ValueError, match="tipo"):
            gw.enviar_orden(NuevaOrden(tipo="HOLD", cantidad=1))  # type: ignore[arg-type]
        assert repo.guardadas == []

    def test_un_fallo_del_repositorio_se_propaga(self) -> None:
        gw, _ = gateway(TransaccionesFalsas(roto=True))
        with pytest.raises(RuntimeError, match="postgres"):
            gw.enviar_orden(NuevaOrden(tipo="BUY", cantidad=1))


class TestPosiciones:
    def test_sin_posiciones_no_hay_liquidacion(self) -> None:
        gw, repo = gateway(TransaccionesFalsas(neta=0.0))
        assert gw.posiciones_abiertas() == []
        assert gw.liquidar_todo() == []
        assert repo.guardadas == []

    def test_posicion_neta_a_negativa_tampoco_cuenta(self) -> None:
        # Vendido de más = no hay posición abierta que cerrar.
        gw, _ = gateway(TransaccionesFalsas(neta=-4.0))
        assert gw.posiciones_abiertas() == []

    def test_liquidar_vende_todo_lo_abierto(self) -> None:
        gw, repo = gateway(TransaccionesFalsas(neta=3.0))
        ordenes = gw.liquidar_todo()

        assert len(ordenes) == 1
        assert repo.guardadas[0].transaction_type == "SELL"
        assert repo.guardadas[0].quantity == 3.0
        assert ordenes[0].is_simulated is True

    def test_el_simbolo_es_el_configurado(self) -> None:
        gw, _ = gateway(simbolo="QQQ")
        assert gw.posiciones_abiertas() == []  # sin posición, sin fila
        gw2, _ = gateway(TransaccionesFalsas(neta=1.0), simbolo="QQQ")
        assert gw2.posiciones_abiertas()[0].simbolo == "QQQ"

    def test_un_fallo_al_vender_no_se_disfraza_de_exito(self) -> None:
        gw, _ = gateway(TransaccionesFalsas(neta=2.0, roto=True))
        with pytest.raises(RuntimeError):
            gw.liquidar_todo()


class TestContrato:
    def test_el_gateway_cumple_el_protocolo(self) -> None:
        # Estructural: si la clase deja de tener estos métodos, falla aquí y no
        # en runtime dentro del motor del Kill-Switch.
        from src.integration.broker import BrokerGateway

        gw, _ = gateway()
        assert isinstance(gw, BrokerGateway)
        for metodo in ("posiciones_abiertas", "enviar_orden", "liquidar_todo"):
            assert callable(getattr(gw, metodo))

    def test_los_tipos_son_los_del_esquema(self) -> None:
        _, repo = gateway()
        gw, _ = gateway(repo)
        gw.enviar_orden(NuevaOrden(tipo="SELL", cantidad=1))
        assert repo.guardadas[0].transaction_type in ("BUY", "SELL")
        assert isinstance(repo.guardadas[0], TransaccionNueva)
        assert isinstance(repo.guardadas[0].execution_time, int)
