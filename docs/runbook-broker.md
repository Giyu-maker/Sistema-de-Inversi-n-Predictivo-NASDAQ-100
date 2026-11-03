# Runbook del operador — fallos de comunicación con el broker (RF-801)

**Para:** operadora y secretaria de RiTech SAS.
**Cuándo:** cuando el panel deje de verse normal (amarillo o rojo), o cuando
se sospeche que el sistema no está recibiendo datos u órdenes.
**Qué es esto:** la secuencia exacta de mitigación que pide **RF-801** (SRS
§8). No sustituye al panel: empírese siempre mirando el panel.

> **Situación actual (Sprint 4):** la comunicación con Interactive Brokers
> todavía no existe: corre `SimulatedBrokerGateway`, que registra órdenes
> **ficticias** en PostgreSQL con `is_simulated = true` (Ítem 12 pendiente,
> CR-006). Por eso hoy no hay caída posible *del broker* — sí de las bases
> de datos, de la API y de la IA, que son los fallos que este runbook cubre
> en detalle. Los pasos del broker real quedan escritos abajo para cuando se
> instale el Ítem 12: son los mismos, y este documento es el que hay que
> actualizar entonces.

---

## 1. Los tres colores del panel (y qué hacer con cada uno)

| Color | Qué significa | Qué hace usted |
|---|---|---|
| **Verde** — «Sistema normal» | Dato fresco, IA disponible, sin bloqueo. | Nada. |
| **Ámbar** — «Atención» / «Revisar el sistema» | Algo falló o está viejo: sin datos, IA caída, base de datos caída, o el estado del Kill-Switch no se pudo verificar. | Siga la §2 según el motivo que aparece al lado del color. |
| **Rojo** — «Kill-Switch activo» | El sistema se detuvo a sí mismo (o alguien lo detuvo). No se emiten órdenes ni sugerencias. | **§4.** |

El motivo que aparece junto al color lo escribe el **backend**, en español
llano: léalo completo antes de actuar, porque ahí está el por qué.

---

## 2. Ámbar: «Revisar el sistema»

Abra una terminal en la Máquina 2 y mire el motivo exacto en el panel. Luego:

**a) «datos desactualizados» (la última vela tiene más de 10 minutos)**
1. `scripts/sesion.sh estado` — ¿inferencia y API «corriendo»?
2. `docker compose logs --tail 50 scheduler` — ¿el ciclo está corriendo y
   fallando, o ni siquiera arrancó?
3. Si el mensaje habla de la fuente de datos (Twelve Data) o de cuota
   agotada: **no toque nada**. La recuperación de huecos corre sola en el
   siguiente tick; solo reabra la sesión con `scripts/sesion.sh start` si el
   scheduler no está corriendo.
4. Si el scheduler está corriendo y sigue sin datos: llame al administrador
   de TI.

**b) «La IA no responde» o «el modelo no está cargado»**
1. En la Máquina 1: `systemctl status ritech-inference`.
2. Si está caída: `systemctl start ritech-inference` y esperar a que
   `/health` responda con `modelo_cargado: true`.
3. El panel vuelve a verde solo. **Nunca** apague la IA para «reiniciar el
   sistema entero»: sin ella no hay predicciones nuevas, pero el sistema
   sigue siendo seguro.

**c) «La base de datos no responde»** (PostgreSQL o InfluxDB)
1. `docker compose ps` — ¿`postgres` e `influxdb` en `healthy`?
2. Si un contenedor no está: `docker compose up -d postgres influxdb` y
   esperar al healthcheck.
3. **No** ejecute `docker compose down` en horario de sesión: borraría el
   stack en caliente. Si hace falta reiniciar un contenedor,
   `docker compose restart postgres` (o `influxdb`) y verificar después con
   `scripts/sesion.sh estado`.
4. Con PostgreSQL caído el sistema **falla cerrado**: no se emiten órdenes.
   Es intencional (CR-006) y se revuelve solo al volver la base.

**d) «No se pudo verificar el estado del Kill-Switch»**
Mismo caso (c): es el estado del bloqueo el que no se pudo leer. Hasta
confirmarlo el sistema no emite órdenes. Siga (c) hasta que el motivo
desaparezca.

---

## 3. Fallo de comunicación con el broker (el caso de RF-801)

**Hoy (simulación):** el «broker» es la propia base de datos, así que no hay
red que falle. Si ve `La liquidación falló: …` en el motivo del
Kill-Switch, el problema es de PostgreSQL o de precios, y el tratamiento es
el de §2 (c).

**Con el Ítem 12 (IB Gateway real) instalado**, la secuencia es:

1. **No reintente a ciegas.** Cada reintento puede duplicar una orden.
   Anote la hora exacta (formato `DD/MM/YYYY HH:mm:ss`).
2. Mire el panel: ¿está **verde** o **rojo**?
   - **Verde:** el sistema sigue operando y la última orden quedó registrada
     en `transactions`. Revise `GET /api/transactions` o la tabla
     `transactions` para confirmar si la orden llegó a registrarse.
   - **Rojo:** el Kill-Switch ya actuó; siga §4.
3. Compruebe el proceso del gateway en la Máquina 2:
   `docker compose logs --tail 100 api | grep -i -E "broker|orden"`.
4. Si la comunicación sigue caída y el panel está verde, **detenga el
   sistema antes de que decida algo sin poder ejecutarlo**: pulse el
   **Kill-Switch** (§4, sin clave) y avise al administrador de TI.
5. Nunca apague el API «para reiniciar la conexión con el broker»: dejaría
   sin estado al panel y perdería el bloqueo en memoria.

**Nunca** reintente manualmente una orden desde el panel: en esta fase el
panel no tiene ese botón, y cuando lo tenga, las órdenes van auditadas.

---

## 4. Rojo: Kill-Switch activo

El sistema está detenido: no emite órdenes ni sugerencias, y así seguirá
hasta un reinicio autorizado.

1. **Lea el motivo.** Distingue dos casos:
   - *«Kill-Switch ACTIVO en este proceso y sin poder guardarse en la base
     de datos»*: la base estaba caída cuando se disparó. El bloqueo está en
     memoria del proceso: **no reinicie el proceso** hasta tener la base
     arriba, o el bloqueo se perdería.
   - El motivo normal (p. ej. «Pérdida de sesión de 1,42 % …»): el estado
     quedó guardado y sobrevive a cualquier reinicio.
2. **Decida si corresponde reiniciar.** El reinicio lo autoriza el
   **Administrador de TI** o el **Gerente**. Si la pérdida sigue vigente o
   no se entiende por qué se disparó, **no reinicie**: dé el sistema
   detenido y escale.
3. **Para reiniciar:** botón «Reiniciar» en la cabecera → escribir la
   **clave de reinicio** → «Confirmar».
   - Sin la clave configurada en el servidor el botón contesta que el
     reinicio no está configurado: pídala al administrador de TI (se genera
     con `python -m src.security.clave_reinicio`).
   - Cinco intentos fallidos bloquean el reinicio unos minutos (límite
     configurable). **No force la clave**: el contador está en la base de
     datos y no se borra reiniciando nada.
4. **Después del reinicio:** el panel vuelve a verde o ámbar y las señales
   se reanudan según el modo vigente. Queda registrado en la auditoría con
   el actor `CLAVE_REINICIO` y la IP del equipo.

---

## 5. Qué NO hacer nunca

- **No** borre, edite ni vacíe `audit_logs`: los triggers lo impiden a
  propósito (RF-701). Si alguien le pide «limpiar la auditoría», escale.
- **No** apague el contenedor `postgres` o `influxdb` para «arreglar» algo
  en horario de sesión.
- **No** reinicie el Kill-Switch «para que el panel se vea verde».
- **No** escriba credenciales, claves ni contraseñas en el chat, en el
  navegador ni en un archivo compartido.
- **No** cambie `is_autonomous` ni los perfiles de riesgo directamente en
  la base de datos: queda sin auditoría (SDD §7 regla 3).

---

## 6. Escalación

| Rol (SDD §3.1.1) | Cuándo |
|---|---|
| `ADMIN_TI` | Cualquier fallo de infraestructura: contenedores, red, M1, claves. |
| `GERENTE` | Decisiones de operación: si reiniciar tras un disparo, si dejar el sistema detenido. |
| `OPERADOR` | Puede disparar el Kill-Switch en cualquier momento (RF-501), sin clave. |

Toda acción desde el panel queda en `audit_logs` con la IP del equipo: si no
queda registrada, **no se hizo**.

---

*Complementos: `docs/despliegue.md` §9 (operación cotidiana) y §10 (fallos
conocidos); `docs/informe-sprint4.md` (cómo se verificó todo esto).*
