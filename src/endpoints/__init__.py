"""API de la Máquina 2 (Sprint 3 Fase B + Sprint 4 Kill-Switch).

Estructura declarada en SDD §6.1 (`endpoints/`): aquí está el ruteador, los
esquemas Pydantic y las dependencias; la lógica de negocio sigue viviendo en
sus módulos (`src/ai`, `src/calculator`, `src/security`, `src/database`).

**Las rutas GET no escriben**: solo `SELECT` y consultas Flux de lectura. Las
únicas escrituras son las del Kill-Switch (`src/endpoints/kill_switch.py`), y
todas quedan auditadas con la IP de la intranet. No hay autenticación ni
roles — RNF-001 («Iniciar Sesión») y RNF-005 siguen como brecha conocida, y
ahora la recoge **CR-006** junto con el reinicio protegido por clave de
reinicio y el disparo sin clave (RF-501). La deuda de mínimo privilegio (rol
de solo lectura) está documentada en `docs/despliegue.md`.
"""
