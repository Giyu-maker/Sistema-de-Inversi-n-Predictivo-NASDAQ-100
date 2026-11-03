"""Integración con el broker: la frontera entre la decisión y el mercado.

Sprint 4 parcial: el Ítem 12 (TCP real con Interactive Brokers) queda fuera de
alcance, así que lo que hay aquí es la **interfaz** que el SDD llama
`BrokerGateway` (§8, RF-402/RF-601) y su implementación **simulada**. El motor
del Kill-Switch y el flujo de órdenes dependen del Protocol, no de la clase
concreta: cambiar a la versión TCP de IB es implementar el mismo Protocol en
este paquete, sin tocar `src/security/`.
"""
