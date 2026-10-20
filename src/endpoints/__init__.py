"""API de solo lectura de la Máquina 2 (Sprint 3, Fase B).

Estructura declarada en SDD §6.1 (`endpoints/`): aquí está el ruteador, los
esquemas Pydantic y las dependencias; la lógica de negocio sigue viviendo en
sus módulos (`src/ai`, `src/calculator`, `src/security`, `src/database`).

**Esta API no escribe.** Ni en PostgreSQL ni en InfluxDB: solo `SELECT` y
consultas Flux de lectura. No hay autenticación ni roles de solo lectura en
esta fase — RNF-001 («Iniciar Sesión») y RNF-005 quedan como brecha conocida
hasta el Sprint 4; la deuda de mínimo privilegio (rol de solo lectura) está
documentada en `docs/despliegue.md`.
"""
