"""Motor de lógica asimétrica — RF-201 (ganancia 2x), RF-202 (límite 1.25%), RF-203 (gaps).

Lógica pura, sin I/O: firma exacta según `docs/sdd.md` §5.2.2.

Regla de negocio central (AGENTS.md §6):
- Ganancia: variación positiva del NASDAQ × 2.0 (apalancamiento 2x).
- Pérdida: pasa sin apalancar; el umbral de contención se evalúa frente al
  precio de entrada con el límite teórico de 1.25%.
- Gap: caída ≥2% de golpe se registra como pérdida real aunque supere el
  1.25% teórico, informando y justificando la diferencia (RF-203).

Decisión de alcance (Sprint 2): FastAPI se introduce en Sprint 3 cuando el
Dashboard necesite exponer este cálculo vía API — hoy no hay endpoint que lo
consuma y montar el framework sin consumidor viola el minimalismo acordado.
"""

from __future__ import annotations


class AsymmetricCalculator:
    """Cálculos de rendimiento asimétrico de RiTech SAS."""

    MAX_LOSS_PCT: float = 1.25  # límite teórico de pérdida (RF-202, system_configurations.max_loss_day)
    GAP_THRESHOLD_PCT: float = 2.0  # caída de golpe que define un gap (RF-203)

    # TODO(RF-204): ventanas de exclusión — no abrir posiciones en los primeros ni
    # los últimos 15 minutos del horario regular de mercado (SRS prioridad Media).
    # Deuda conocida del Sprint 2, registrada en docs/backlog.md para Sprint 3.
    # No implementar a medias: requiere decidir el estado de sesión completo.

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

    @staticmethod
    def is_market_gap(nasdaq_variation_pct: float) -> bool:
        """Detecta un gap de mercado: caída de golpe ≥2% sin operaciones intermedias (RF-203)."""
        return nasdaq_variation_pct <= -AsymmetricCalculator.GAP_THRESHOLD_PCT

    @staticmethod
    def gap_loss_notice(nasdaq_variation_pct: float) -> str:
        """Informa la pérdida real vs. el límite teórico y justifica la diferencia (RF-203).

        Debe usarse solo cuando `is_market_gap()` sea True; la pérdida real se
        registra vía `calculate_yield()` (passthrough de valores negativos).
        """
        difference_pp = abs(nasdaq_variation_pct) - AsymmetricCalculator.MAX_LOSS_PCT
        return (
            f"GAP DE MERCADO: caída de {nasdaq_variation_pct:.2f}% sin operaciones intermedias. "
            f"Pérdida real registrada: {nasdaq_variation_pct:.2f}%, superior al límite teórico de "
            f"-{AsymmetricCalculator.MAX_LOSS_PCT:.2f}% en {difference_pp:.2f} puntos porcentuales. "
            "Justificación: el salto se produjo sin posiciones intermedias que mitigaran el golpe, "
            "por lo que corresponde registrar la pérdida materializada y no la teórica (RF-203)."
        )
