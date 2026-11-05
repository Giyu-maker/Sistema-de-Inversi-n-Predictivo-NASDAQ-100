# Informe Sprint 4 — Seguridad operativa y cierre (RiTech-NASDAQ100)

**Alcance:** Ítem 9 (Kill-Switch), Ítem 10 (auditoría inmutable) y parte del
Ítem 12 (broker simulado), con el **recorte de alcance aprobado**: sin S4.2
(login y roles) y con S4.4 reducido a pruebas de integración.
**Fecha:** 03/11/2026 · **Equipo:** Naranja's Team.

---

## 1. Construcción (un commit por bloque)

| Commit | Bloque | Contenido |
|---|---|---|
| `cfbb492` | **C1** — Auditoría inmutable | Columna `actor`, triggers de fila + `TRUNCATE`, `ENABLE ALWAYS`, `REVOKE`, `src/security/auditoria.py` con la regla estricta |
| `7c90e29` | **C2** — Estado y motor | `kill_switch_state`, `src/integration/broker.py`, `KillSwitchEngine` (bandera → CAS → liquidación → auditoría), cola `logs/auditoria-pendiente.jsonl` |
| `d8d4fd6` | **C3** — API y clave | `GET/POST /api/kill-switch`, `POST …/reset` con clave, `/api/status` con `ROJO`, `RiskManager`, `errores.py` con `ErrorDeAuditoria` |
| `e896b1b` | **C4** — Disparo automático | Umbral −1,25 % por tick, `apertura_de_sesion()` única, guarda anti-bucle, RNF-010 medido, `tests/test_flujo_ordenes.py` |
| `c773e21` | **C5** — UI | Control en la cabecera (2 toques), chip `ROJO` con parpadeo lento, esquemas Zod, escenario `kill-switch-activo` |
| *(este)* | **C6** — Cierre | CR-005 y CR-006, runbook RF-801, este informe, `AGENTS.md` |

**Pruebas:** 878 en Python (`pytest -q`), 47 en frontend (`node --test`),
`oxlint` y `vite build` sin avisos. Las 668 del Sprint 3 siguen en verde:
no se rompió ninguna.

---

## 2. Qué quedó construido

### Auditoría inmutable (RF-701, SDD §7 regla 3)

- `audit_logs` gana la columna **`actor`** (`SISTEMA` | `OPERADOR` |
  `CLAVE_REINICIO`), **sin default permanente**: si se olvida, la fila no se
  inserta. La migración va en un bloque `DO` **antes** de los triggers,
  porque su backfill es un `UPDATE`.
- **La inmutabilidad es de la base de datos, no del código** (lección de
  CR-003): `BEFORE UPDATE OR DELETE` por fila, `BEFORE TRUNCATE` por
  sentencia —los de fila no se disparan ante un `TRUNCATE`—, ambos en
  `ENABLE ALWAYS`, más `REVOKE UPDATE, DELETE, TRUNCATE … FROM PUBLIC`.
- `auditar_sincrono()` **propaga** el fallo: sin auditoría no hay acción,
  para órdenes, cambio de modo y reinicio. Excepción única y declarada: el
  Kill-Switch (§4).

### Kill-Switch (RF-501/502/503, DD-04, RNF-010)

- **Estado persistido** en `kill_switch_state`: sobrevive a reinicios, con
  el recorrido de la liquidación, el acumulado y la guarda anti-bucle.
- **Orden aprobado:** (a) bandera en memoria **sin I/O** → (b)
  compare-and-set en la base → (c) liquidación por `BrokerGateway` →
  (d) auditoría. La regla 3 estricta **no** aplica aquí; sí al resto.
- **Idempotencia:** el `UPDATE … WHERE activo = FALSE RETURNING` hace que
  pulsar dos veces no liquidé dos veces — verificado en vivo, no supuesto.
- **Si la base está caída:** el bloqueo y la liquidación siguen (manda la
  bandera del proceso), el evento queda en `logs/auditoria-pendiente.jsonl`
  y se vuelve a volcar al arrancar; ese archivo además **repara** un bloqueo
  que no llegó a persistir, para que un reinicio no abra el sistema en
  silencio (RF-503).
- **Fallo cerrado:** si el estado no se lee, no se emiten órdenes y el
  panel dice «Revisar el sistema» — `None` nunca se traduce en «operable».
- **Disparo automático** en cada tick contra la apertura de la sesión,
  actor `SISTEMA`, con la pérdida real en el payload cuando es un gap
  (RF-203). Tras un reinicio no se re-evalúa hasta la siguiente sesión.

### API y clave de reinicio

| Ruta | Exigencia |
|---|---|
| `GET /api/kill-switch` | solo lectura |
| `POST /api/kill-switch` | **sin clave** (RF-501) |
| `POST /api/kill-switch/reset` | clave de reinicio (PBKDF2 en el entorno, `compare_digest`, límite de intentos persistido) |

`/api/status` gana `kill_switch` y el estado **`ROJO`**, que manda sobre
todo —incluso sobre «Mercado cerrado»—; con el bloqueo (o sin poder
verificarlo) la señal responde **no operable** con el texto del backend.

### Flujo de órdenes (S4.4 reducido)

`RiskManager.decidir()` en el orden aprobado: bloqueo → señal única
(`STABLE` nunca abre) → modo. Asesor ⇒ alerta y **cero** transacciones;
Autónomo ⇒ orden simulada con `is_simulated = true` y su `prediction_id`.

---

## 3. Verificación contra PostgreSQL real

Todo lo anterior se comprobó con el motor de verdad, no con supuestos:

| Qué | Cómo |
|---|---|
| `UPDATE` / `DELETE` rechazados | INSERT de control en la **misma transacción** que la sentencia; psql sale con `ON_ERROR_STOP` y la transacción se revuelve sola → **cero marcadores** |
| `TRUNCATE` rechazado | ídem, con el trigger de sentencia (el de fila no llega) |
| `ENABLE ALWAYS` | `tgenabled = 'A'` leído de `pg_trigger` para los dos |
| `REVOKE` | con un rol **no superusuario** creado y revertido en la misma transacción (para el superusuario del servicio los privilegios dan `t`: por eso el trigger es la garantía real) |
| Idempotencia del DDL | el esquema completo aplicado **dos veces** seguidas |
| Idempotencia del disparo | compare-and-set en vivo: una sola ganancia por activación; acumulado de liquidaciones entre reinicios |
| Motor del API | montaje, siembra y `resumen()` contra la base real |

**Riesgo residual declarado** (CR-005): el servicio entra como superusuario,
que salta los permisos pero **no** los triggers. Un rol de mínimo privilegio
queda como trabajo futuro.

---

## 4. RNF-010 — interpretación y qué mide cada cifra

**Interpretación escrita** (no aplica literalmente en Python: no hay
prioridades de hilo utilizables sin ser root):

1. el camino de activación **no tiene ni un `await` ni una llamada a la
   base antes** de poner la bandera;
2. todo lo que emite órdenes consulta la bandera **antes** que cualquier
   cálculo;
3. las tareas de orden pendientes se cancelan al activarse;
4. se miden pulsación→bloqueo y pulsación→liquidación.

### 4.1 Qué mide exactamente cada cifra

Las cuatro medidas son de **cuatro pasos distintos y ninguna incluye a
otra**. Es lo que hay que leer antes de mirar los números:

| Cifra | Qué cronometra | Contra qué corre | Qué **excluye** |
|---|---|---|---|
| **Bandera en memoria** (`ms_bloqueo`) | solo `BanderaBloqueo.activar()` | nada: sin I/O, sin red, sin disco | la base, la liquidación, la auditoría, el HTTP |
| **Compare-and-set** (`ms_persistencia`) | solo `UPDATE … WHERE activo = FALSE` + `commit` de `kill_switch_state` | **PostgreSQL real** (`ritech-postgres16`, en la red local) | la bandera, la liquidación, la auditoría |
| **Liquidación** (`ms_liquidacion`) | solo `BrokerGateway.liquidar_todo()` | **`SimulatedBrokerGateway`** con repositorio de transacciones **en memoria** | la base, la auditoría — y **no es IB real** |
| **Pulsación → respuesta HTTP** | la petición `POST /api/kill-switch` entera, hasta tener la respuesta | dos variantes: con dobles de base, y con **PostgreSQL real** | el `INSERT` de `audit_logs` |

**Tres precisiones que no admiten ambigüedad:**

- **La liquidación es simulada.** Las cifras de la fila 3 salen de
  `SimulatedBrokerGateway` sobre un repositorio en memoria: miden el
  camino de verdad del código (calcular posiciones, pedir precio, construir
  la transacción) **sin** escribir en `transactions` y **sin** red hacia un
  broker. **El tiempo contra Interactive Brokers real queda SIN MEDIR**: el
  Ítem 12 (TCP con IB Gateway) no existe todavía.
- **El `INSERT` de `audit_logs` tampoco está en ninguna cifra.** Medirlo
  exigiría escribir en una tabla inmutable — no habría forma de limpiar la
  medición —, así que se excluye. Es un `INSERT` simple, del mismo tipo que
  el compare-and-set de la fila 2.
- **Las cifras no se suman entre sí**, pero tampoco son despreciables unas
  respecto a otras: el compare-and-set contra PostgreSQL real cuesta
  **~8 ms**, que es órdenes de magnitud más que la bandera (~3 µs). Es
  exactamente por eso que el orden del motor es **bandera primero y base
  después**: el bloqueo en el proceso no espera a la red.

### 4.2 Cifras

Medidas con `pytest -s`, 05/11/2026 (10–50 disparos por fila):

| Cifra | p50 | p95 |
|---|---|---|
| Bandera en memoria (sin I/O) | **0,003 ms** | **0,004 ms** |
| Compare-and-set contra **PostgreSQL real** | **7,81 ms** | **11,83 ms** |
| Compare-and-set con repositorio en memoria | 0,002 ms | 0,004 ms |
| Liquidación — **SimulatedBrokerGateway**, repo en memoria | **0,027 ms** | **0,038 ms** |
| Pulsación → respuesta HTTP, motor con dobles | **1,37 ms** | **1,66 ms** |
| Pulsación → respuesta HTTP + **PostgreSQL real** (sin auditoría) | **28,58 ms** | **40,06 ms** |

*Las cifras varían entre corridas sin cambiar de magnitud (una corrida
previa dio 2,56 ms de p95 en el HTTP con dobles y 9,20 ms en el CAS real).
Todas quedan muy por debajo de la cota de 500 ms de RNF-003.*

### 4.3 Por qué 28 ms en lugar de 1,8 ms

La fila 6 es la única que muestra el camino **completo** con la base real:
bandera + CAS + marcar `EN_CURSO` + marcar `COMPLETADA` + el `resumen()` que
se devuelve al panel. Son **cuatro idas y vueltas** a PostgreSQL de ~7–8 ms
cada una (conexión nueva por operación, sin *pool*: decisión documentada en
`src/database/pg.py`), y solo una de ellas es el compare-and-set. La fila 5
es el mismo camino sin base, y la diferencia entre las dos es, literalmente,
el coste de la persistencia.

El requisito que RNF-010 pide proteger —**bloquear antes de liquidar**— se
cumple en los primeros **0,003 ms** de la fila 1; todo lo demás ocurre ya
con el sistema bloqueado.

---

## 5. Brechas declaradas y trabajo futuro

Las brechas están en **`docs/change-requests.md` CR-006** (autenticación
mínima) y **CR-005** (riesgo del superusuario). Resumen:

- **Sin autenticación de usuarios ni roles** (S4.2 eliminado): sin login
  (RNF-001), sin autorización por rol (RNF-005), sin TOTP para el Modo
  Autónomo (SRS §3.4.2, RF-402) y sin identidad personal en el reinicio
  (RF-503) — solo el actor `CLAVE_REINICIO` con la IP.
- **Las rutas GET siguen abiertas** (CR-004 se mantiene en lo esencial);
  las únicas escrituras son las del Kill-Switch.
- **Sin TLS** en la intranet.

Trabajo futuro documentado (también en `docs/despliegue.md` §12):

| Ítem | Qué queda |
|---|---|
| **11** | Notificaciones SMS/SMTP (módem GSM o SMTP local) |
| **12** | IB Gateway real por TCP → implementar `BrokerGateway` en `src/integration/` |
| **13** | Reportes financieros PDF/CSV (RF-702) |
| — | TLS, TOTP, `trader_users` + sesiones, rol de mínimo privilegio en PostgreSQL |
| — | **RF-204** (ventanas de exclusión) — sigue con su `TODO(RF-204)` |
| — | **Verificación física en M1**: aquí todo se corrió en M2 con la inferencia en CPU |

---

## 6. Cómo se corrieron las cifras

```bash
# Pruebas (contenedor PostgreSQL levantado para la parte viva)
docker compose up -d postgres
uv run --frozen --group ai --group ai-cpu pytest -q -s   # incluye RNF-010
docker compose stop postgres                             # entorno como estaba

# Frontend
cd frontend && npm test && npm run lint && npm run build
```

Las pruebas vivas se omiten solas (`skip`) si el contenedor no responde, así
que la suite completa también corre sin base de datos.

---

## 7. Notas para la Gerencia

1. **El Sprint 4 quedó mínimo y cerrado**: Ítem 9 completo, Ítem 10
   completo, Ítem 12 solo en su parte simulada. Los Ítems 11 y 13 no
   empezaron.
2. **Lo más importante que se ganó** no es el botón, es que el botón
   *funciona cuando todo lo demás falla*: ni la base caída ni la auditoría
   caída impiden detener el sistema, y el estado no se pierde.
3. **Lo más importante que se dejó abierto** es que cualquiera que llegue
   al panel puede disparar (bien, es una emergencia) y **reiniciar con la
   clave**: hasta que existan usuarios y roles, la disciplina operativa es
   parte del control (runbook §4 y §6).
