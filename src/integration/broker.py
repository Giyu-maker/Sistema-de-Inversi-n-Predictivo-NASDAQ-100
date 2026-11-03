"""`BrokerGateway` + `SimulatedBrokerGateway` (RF-402, RF-601, RF-602).

El Protocol y la clase simulada
-------------------------------
`BrokerGateway` es la frontera que el SDD §5.4/§8 nombra para el RiskManager y
el Kill-Switch. La única implementación de este sprint es
`SimulatedBrokerGateway`: **datos reales, órdenes ficticias** (RF-601), que se
guardan con `is_simulated = true` y, cuando existe, ligadas a su
`prediction_id` (RF-602) para poder cruzar después cada transacción con la
decisión de la IA que la originó.

Qué NO hace (y queda documentado como brecha en CR-006):
* no abre ningún socket TCP: no hay IB Gateway en esta fase;
* no hay cuenta Sandbox real: el «broker» es la propia base de datos.

De dónde salen los precios y las posiciones
-------------------------------------------
* **Precio**: de un callable inyectado. En producción lo conecta el API al
  lector de InfluxDB; en los tests, un fake. El gateway no sabe de dónde
  viene el precio, solo que pedirlo puede fallar — y si falla, la liquidación
  falla en lugar de inventar un número.
* **Posiciones**: de la diferencia entre comprado y vendido en `transactions`.
  El esquema es de instrumento único (QQQ como proxy del NASDAQ-100), así que
  no hay columna de símbolo: `Posicion.simbolo` es la etiqueta que configura
  el gateway.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable, Literal, Protocol, runtime_checkable
from uuid import uuid4

from src.database.transacciones import (
    RepositorioTransacciones,
    TransaccionNueva,
)

logger = logging.getLogger(__name__)

TipoOrden = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class NuevaOrden:
    """Orden que alguien pide emitir (decisión, no ejecución)."""

    tipo: TipoOrden
    cantidad: float
    prediction_id: str | None = None


@dataclass(frozen=True)
class Orden:
    """Orden registrada, tal como quedó en `transactions`."""

    id: str
    broker_order_id: str
    tipo: str
    cantidad: float
    precio: float
    is_simulated: bool
    prediction_id: str | None


@dataclass(frozen=True)
class Posicion:
    simbolo: str
    cantidad: float


@runtime_checkable
class BrokerGateway(Protocol):
    """Superficie que consumen `KillSwitchEngine` y el flujo de órdenes.

    `runtime_checkable` para poder comprobar el contrato en los tests: si una
    implementación deja de tener uno de los tres métodos, falla ahí y no en
    medio de una liquidación.
    """

    def posiciones_abiertas(self) -> list[Posicion]: ...

    def enviar_orden(self, orden: NuevaOrden) -> Orden:
        """Emite una orden. **Propaga** el fallo: nunca devuelve una orden fingida."""
        ...

    def liquidar_todo(self) -> list[Orden]:
        """Vende toda la posición abierta al precio de mercado (RF-502)."""
        ...


class SimulatedBrokerGateway:
    """Modo Sombra: todo se registra como simulado, nada toca capital."""

    def __init__(
        self,
        transacciones: RepositorioTransacciones,
        *,
        precio_actual: Callable[[str], float],
        simbolo: str = "QQQ",
        ahora_ms: Callable[[], int] | None = None,
        nuevo_id: Callable[[], str] | None = None,
    ) -> None:
        self._transacciones = transacciones
        self._precio_actual = precio_actual
        self._simbolo = simbolo
        self._ahora_ms = ahora_ms
        self._nuevo_id = nuevo_id

    def _ahora(self) -> int:
        if self._ahora_ms is not None:
            return self._ahora_ms()
        return int(time.time() * 1000)

    def _id_orden(self) -> str:
        if self._nuevo_id is not None:
            return self._nuevo_id()
        return f"SIM-{uuid4()}"

    def posiciones_abiertas(self) -> list[Posicion]:
        neta = self._transacciones.cantidad_neta()
        if neta <= 0:
            return []
        return [Posicion(simbolo=self._simbolo, cantidad=neta)]

    def enviar_orden(self, orden: NuevaOrden) -> Orden:
        if orden.tipo not in ("BUY", "SELL"):
            raise ValueError(f"tipo de orden inválido: {orden.tipo!r}")
        if orden.cantidad <= 0:
            raise ValueError(f"la cantidad debe ser mayor que cero: {orden.cantidad!r}")

        # Pedir el precio ANTES de escribir: si la fuente de precios no
        # responde, no queda una orden con precio inventado.
        precio = float(self._precio_actual(self._simbolo))
        if precio <= 0:
            raise ValueError(f"precio de mercado no válido: {precio!r}")

        transaccion = TransaccionNueva(
            broker_order_id=self._id_orden(),
            transaction_type=orden.tipo,
            quantity=float(orden.cantidad),
            execution_price=precio,
            execution_time=self._ahora(),
            prediction_id=orden.prediction_id,
            is_simulated=True,  # RF-601/RF-602: SIEMPRE simulado en esta fase
        )
        identificador = self._transacciones.guardar(transaccion)
        logger.info(
            "Orden simulada registrada: %s %s x %s @ %s (predicción %s)",
            orden.tipo, orden.cantidad, self._simbolo, precio,
            orden.prediction_id or "sin vínculo",
        )
        return Orden(
            id=identificador,
            broker_order_id=transaccion.broker_order_id,
            tipo=transaccion.transaction_type,
            cantidad=transaccion.quantity,
            precio=transaccion.execution_price,
            is_simulated=True,
            prediction_id=transaccion.prediction_id,
        )

    def liquidar_todo(self) -> list[Orden]:
        """Vende la posición neta abierta (RF-502). Sin posición: lista vacía.

        Que no haya nada que vender NO es un error: el bloqueo ya está puesto
        (RF-503) y el registro de una liquidación sin posiciones es justamente
        lo que prueba que el sistema intentó liquidar.
        """
        ordenes: list[Orden] = []
        for posicion in self.posiciones_abiertas():
            ordenes.append(
                self.enviar_orden(NuevaOrden(tipo="SELL", cantidad=posicion.cantidad))
            )
        if not ordenes:
            logger.warning("Liquidación sin posiciones abiertas: solo se registra el bloqueo.")
        return ordenes
