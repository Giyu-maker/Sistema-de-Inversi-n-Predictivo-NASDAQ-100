"""Motor de lógica asimétrica — RF-201 (ganancia 2x) y RF-202 (límite 1.25%).

Lógica pura, sin I/O: firma exacta según `docs/sdd.md` §5.2.2.

Regla de negocio central (AGENTS.md §6):
- Ganancia: variación positiva del NASDAQ × 2.0 (apalancamiento 2x).
- Pérdida: pasa sin apalancar; el umbral de contención se evalúa frente al
  precio de entrada con el límite teórico de 1.25%.
"""

from __future__ import annotations


class AsymmetricCalculator:
    """Cálculos de rendimiento asimétrico de RiTech SAS."""

    MAX_LOSS_PCT: float = 1.25  # límite teórico de pérdida (RF-202, system_configurations.max_loss_day)

    @staticmethod
    def calculate_yield(nasdaq_variation_pct: float) -> float:
        """Retorna el rendimiento de RiTech.

        Si la variación es positiva, multiplica por 2 (2x, RF-201).
        Si es negativa, retorna la pérdida sin apalancar.
        """
        if nasdaq_variation_pct >= 0:
            return nasdaq_variation_pct * 2.0
        else:
            return nasdaq_variation_pct

    @staticmethod
    def is_threshold_breached(entry_price: float, current_price: float) -> bool:
        """Verifica de forma estricta si la pérdida supera el 1.25% (RF-202).

        Se dispara cuando la variación es menor o igual a -1.25% (el caso
        borde exacto de -1.25% sí está dentro del umbral de disparo).
        """
        if entry_price <= 0:
            raise ValueError("entry_price debe ser mayor que 0.")
        variation = ((current_price - entry_price) / entry_price) * 100
        return variation <= -AsymmetricCalculator.MAX_LOSS_PCT
