"""`transactions` — órdenes reales y simuladas (RF-401/402, RF-601/RF-602).

Qué se registra y por qué
-------------------------
Cada orden que el sistema emite —real o ficticia— queda aquí con su
`prediction_id` cuando existe: ese vínculo es lo que RF-602 y SRS §4.3
piden para poder cruzar después cada transacción con la decisión de la IA
que la originó, con su confianza y su justificación XAI.

**`is_simulated` es TRUE por defecto y es la marca de Modo Sombra** (RF-601):
en esta fase todo lo que sale es ficticio, y la columna es lo que separa esas
filas de una futura operación real. Nada de este módulo puede escribir
`FALSE` sin pasar por una decisión explícita de quien lo llame.

El CHECK del esquema se refleja en `_validar` (mismo motivo que en
`predictions.py`): fallar en el caller con un mensaje claro es mejor que un
error de restricción a mitad de un INSERT.

Posiciones abiertas
-------------------
El esquema es de instrumento único (QQQ como proxy del NASDAQ-100), así que
una posición «abierta» es la diferencia entre lo comprado y lo vendido. No
hay columna de símbolo porque no la designa el SDD §3.1.1; añadirla sería
un cambio de esquema, no una omisión de esta capa.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from src.database import pg

TipoOrden = Literal["BUY", "SELL"]

_INSERTAR = """
    INSERT INTO transactions
        (prediction_id, broker_order_id, transaction_type, quantity,
         execution_price, execution_time, is_simulated)
    VALUES (%s, %s, %s, %s, %s, %s, %s)
    RETURNING id
"""

#: Comprado menos vendido. SUMA TODAS las filas (simuladas y reales): una
#: posición real también habría que liquidarla, y el Modo Sombra no cambia esa
#: aritmética. La separación de mundos la mantiene `is_simulated` al leer.
_NETO = """
    SELECT COALESCE(SUM(CASE WHEN transaction_type = 'BUY' THEN quantity
                             ELSE -quantity END), 0)
    FROM transactions
"""

_LISTAR = """
    SELECT id, prediction_id, broker_order_id, transaction_type, quantity,
           execution_price, execution_time, is_simulated
    FROM transactions
    ORDER BY execution_time DESC, id
    LIMIT %s OFFSET %s
"""

_CONTAR = "SELECT count(*) FROM transactions"


@dataclass(frozen=True)
class TransaccionNueva:
    """Lo que un gateway intenta persistir."""

    broker_order_id: str
    transaction_type: TipoOrden
    quantity: float
    execution_price: float
    execution_time: int  #: epoch ms
    prediction_id: str | None = None
    is_simulated: bool = True


@dataclass(frozen=True)
class TransaccionGuardada:
    """Una fila leída de `transactions`."""

    id: str
    prediction_id: str | None
    broker_order_id: str
    transaction_type: str
    quantity: float
    execution_price: float
    execution_time: int
    is_simulated: bool


class RepositorioTransacciones(Protocol):
    """Superficie que consume `SimulatedBrokerGateway` (y que los tests falsifican)."""

    def guardar(self, transaccion: TransaccionNueva) -> str:
        """Inserta la fila y devuelve su id. **Propaga** cualquier fallo."""
        ...

    def cantidad_neta(self) -> float:
        """Comprado menos vendido (positivo = posición abierta)."""
        ...

    def listar(self, limit: int, offset: int) -> tuple[list[TransaccionGuardada], int]:
        """Página (más recientes primero) y total de filas."""
        ...


def _validar(t: TransaccionNueva) -> None:
    """Espejo de los CHECK del esquema, antes de tocar la base."""
    if t.transaction_type not in ("BUY", "SELL"):
        raise ValueError(f"tipo de transacción inválido: {t.transaction_type!r}")
    if t.quantity <= 0:
        raise ValueError(f"la cantidad debe ser mayor que cero: {t.quantity!r}")
    if t.execution_price < 0:
        raise ValueError(f"el precio de ejecución no puede ser negativo: {t.execution_price!r}")
    if not t.broker_order_id.strip():
        raise ValueError("el identificador de orden del broker es obligatorio")
    if len(t.broker_order_id) > 100:
        raise ValueError("el identificador de orden no puede superar 100 caracteres")
    if t.prediction_id is not None:
        # Es una FK a `ai_predictions.id` (UUID): validar aquí evita un error
        # de tipo opaco de PostgreSQL en medio del INSERT.
        try:
            UUID(t.prediction_id)
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValueError(
                f"prediction_id no es un UUID válido: {t.prediction_id!r}"
            ) from exc


def _fila(tupla: tuple) -> TransaccionGuardada:
    (identificador, prediccion, orden, tipo, cantidad, precio, tiempo, simulada) = tupla
    return TransaccionGuardada(
        id=str(identificador),
        prediction_id=str(prediccion) if prediccion is not None else None,
        broker_order_id=str(orden),
        transaction_type=str(tipo),
        quantity=float(cantidad),
        execution_price=float(precio),
        execution_time=int(tiempo),
        is_simulated=bool(simulada),
    )


class TransaccionesPostgres:
    """Implementación real sobre `transactions` (`src/database/schema.sql`)."""

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = dsn

    def guardar(self, transaccion: TransaccionNueva) -> str:
        _validar(transaccion)
        with pg.conexion(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    _INSERTAR,
                    (
                        transaccion.prediction_id,
                        transaccion.broker_order_id,
                        transaccion.transaction_type,
                        float(transaccion.quantity),
                        float(transaccion.execution_price),
                        transaccion.execution_time,
                        transaccion.is_simulated,
                    ),
                )
                fila = cur.fetchone()
        return str(fila[0])

    def cantidad_neta(self) -> float:
        with pg.conexion(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(_NETO)
                fila = cur.fetchone()
        return float(fila[0]) if fila else 0.0

    def listar(self, limit: int, offset: int) -> tuple[list[TransaccionGuardada], int]:
        with pg.conexion(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(_LISTAR, (limit, offset))
                filas = cur.fetchall()
                cur.execute(_CONTAR)
                total = int(cur.fetchone()[0])
        return [_fila(f) for f in filas], total
