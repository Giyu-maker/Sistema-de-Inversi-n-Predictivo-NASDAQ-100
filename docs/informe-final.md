# Informe final — Sistema de Inversión Predictivo NASDAQ-100 (RiTech)

**Cliente:** RiTech SAS · **Equipo:** Naranja's Team · **Fecha:** 05/11/2026
**Alcance cubierto:** Sprints 1–4 (planeación, walking skeleton, cerebro
predictivo + panel, y seguridad operativa).

Este documento es el **índice**: no repite contenido, enlaza dónde está cada
verificación y lista —sin adornos— lo que falta. Si algo de este proyecto
hay que revisar en dos minutos, se empieza por la **matriz de cumplimiento**.

---

## 1. Índice de informes

| Documento | Qué cubre | Fecha |
|---|---|---|
| **[matriz-cumplimiento.md](matriz-cumplimiento.md)** | **Los 39 requisitos del SRS con estado, evidencia, brecha y CR** + índice CR-001…CR-006 | 05/11/2026 |
| [informe-sprint4.md](informe-sprint4.md) | Kill-Switch, auditoría inmutable, disparo automático, RNF-010 desglosado, verificación contra PostgreSQL real | 03/11/2026 |
| [informe-b2-sprint3.md](informe-b2-sprint3.md) | Dashboard, RNF-003 por componentes, RNF-004, capturas y revisión | 25/10/2026 |
| [informe-b1-sprint3.md](informe-b1-sprint3.md) | API de M2, ciclo `scheduler → M1 → ai_predictions`, benchmark RNF-003 | 20/10/2026 |
| [informe-entrenamiento-sprint3.md](informe-entrenamiento-sprint3.md) | Entrenamiento del modelo, métricas, XAI y límites del artefacto | 17/10/2026 |
| [change-requests.md](change-requests.md) | **CR-001 … CR-006**: qué se decidió, por qué y con qué impacto | 17/10 → 05/11/2026 |
| [runbook-broker.md](runbook-broker.md) | RF-801: qué hace el operador ante cada fallo | 03/11/2026 |
| [despliegue.md](despliegue.md) | Instalación por máquina, variables de entorno, operación cotidiana | 25/10/2026 |
| [backlog.md](backlog.md) | Planificación de sprints y estado de cada ítem | 05/11/2026 |
| [srs.md](srs.md) · [sdd.md](sdd.md) | Requisitos (v1.1) y diseño (v1.1), fuentes de verdad | 17/10/2026 |

**Estados en una mirada** (matriz §Resumen): **15 Cumple · 21 Parcial ·
0 No cumple · 3 Futuro** de 39 requisitos. «Parcial» no es un eufemismo de
«hecho»: significa que la verificación fue solo con datos inyectados, con el
broker simulado, o sin observación en vivo — la matriz dice cuál en cada
fila.

---

## 2. Brechas honestas

Ninguna de estas está escondida: todas tienen fila en la matriz y, si
afecta a un requisito aprobado, su CR.

### Bloquean el «go-live» real

1. **Sin autenticación** (CR-006): no hay login, ni usuarios, ni roles, ni
   TOTP. Cualquiera que alcance el panel ve todo; el reinicio del
   Kill-Switch se protege con una clave, no con una identidad (RF-503,
   RNF-001, RNF-005, SRS §3.4.2).
2. **Broker simulado**: `SimulatedBrokerGateway` hace todo el trabajo, pero
   **no hay TCP con Interactive Brokers** (Ítem 12). Ninguna orden, ni la
   liquidación del Kill-Switch, ha tocado un mercado real (RF-402, RF-502,
   RF-601, RNF-009, RNF-010).
3. **IA verificada en CPU**: el modelo se entrenó y midió en M2; **M1 con
   su GTX 1660 SUPER sigue sin verificar** el entrenamiento diario, la
   inferencia ni la latencia con CUDA (RF-301, RF-302).

### Medio plazo

4. **RNF-003 sin cerrar**: 4 de 5 componentes por debajo de 500 ms (p95
   117 ms), pero falta la observación **end-to-end con una vela real**.
5. **RF-102 parcial**: el plan *Basic* de Twelve Data solo cubre el horario
   regular (78 de ~192 velas): sin pre-market ni after-hours (CR-002).
6. **Sin interfaz de configuración**: los perfiles de riesgo y el modo se
   cambian escribiendo en la base (RF-403, RNF-002).
7. **Retención de 5 años sin mecanismo**: la inmutabilidad está verificada,
   pero no hay herramienta de purga para ADMIN_TI (SRS §4.4) ni medición de
   retención (RF-701).
8. **Sin medición de disponibilidad**: healthchecks sí, acumulado del
   99,9 % no (RNF-007).
9. **Captura end-to-end del Kill-Switch pendiente**: el escenario
   `kill-switch-activo` está listo para la verificación visual en navegador
   (RF-501).

### Menores o de proceso

10. **RF-204 sin implementar** (ventanas de exclusión de 15 min): deuda
    desde el Sprint 2, con su `TODO(RF-204)`.
11. **Sin TLS** en la intranet y sin rol de mínimo privilegio en
    PostgreSQL (deuda D4 de CR-004).
12. **Sin test dedicado** de `src/security/crypto.py` (RNF-006) ni de la
    latencia de la auditoría en producción.
13. **REST-03 y REST-04** no se pueden verificar desde el repositorio:
    presupuesto y fecha límite los confirma la Gerencia.

---

## 3. Trabajo futuro

| Prioridad | Qué | Dónde está anotado |
|---|---|---|
| Alta | **Ítem 12**: IB Gateway por TCP → implementar `BrokerGateway` en `src/integration/` | backlog, CR-005 |
| Alta | **Autenticación**: `trader_users` + sesiones + roles + TOTP, y rol de mínimo privilegio (D4) | CR-006, CR-004 |
| Alta | **Verificación física en M1** (CUDA, entrenamiento diario, inferencia) | informe-sprint4 §5 |
| Media | **Ítem 11**: notificaciones SMS/SMTP (módem GSM o SMTP local) | backlog, despliegue §12 |
| Media | **Ítem 13**: reportes financieros PDF/CSV (RF-702) | backlog, despliegue §12 |
| Media | **Cierre de RNF-003** con observación end-to-end en vela real | backlog, informe B2 |
| Media | **RF-204**: ventanas de exclusión por volatilidad | backlog, `asymmetric.py` |
| Media | **Captura y revisión** del control del Kill-Switch (`break-ui` sobre `kill-switch-activo`) | AGENTS.md §17.3.4 |
| Baja | TLS en la intranet · herramienta de purga (SRS §4.4) · test de `crypto.py` · RF-102 ampliado (plan de Twelve Data) | CR-004, SRS §4.4 |
| Programada | **REST-06**: pruebas de penetración en la fase final (noviembre 2026) | SRS §8 |

---

## 4. Cómo se verificó el proyecto

```bash
# Backend (las vivas se omiten solas sin el contenedor)
docker compose up -d postgres
uv run --frozen --group ai --group ai-cpu pytest -q    # 927 pruebas
docker compose stop postgres

# Demo manual de la inmutabilidad de `audit_logs` (RF-701): dos INSERT
# aceptados y tres ERROR del trigger, cada ataque en su transacción con
# ROLLBACK, sin dejar filas detrás.
docker exec -i ritech-postgres16 sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < scripts/demo_inmutabilidad.sql

# Frontend
cd frontend && npm test && npm run lint && npm run build   # 61 pruebas

# Servicios (ver docs/despliegue.md §11.1)
scripts/sesion.sh estado
```

**Última corrida (05/11/2026):** 927 pruebas Python + 61 de frontend en
verde; `oxlint` y `vite build` sin avisos. Las mediciones de RNF-010 y de
RNF-003 están en sus informes con la descripción exacta de qué mide cada
cifra.
