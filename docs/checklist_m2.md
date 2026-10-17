# Checklist de verificación — Máquina 2 (Nodo Central)

Procedimiento de despliegue y verificación del Walking Skeleton (Sprint 2) en la
Máquina 2. **Verificado en vivo el 06/10/2026 en esta misma máquina** (Docker
29.8.2, docker compose 5.6.0): los puntos ya ejecutados aparecen marcados con
su evidencia; los no marcados siguen pendientes.

> **Nota de entorno:** cargar el env con `source` **separado** por archivo.
> `source .env .env.local` solo importa `.env` (los demás archivos se tratan como
> argumentos posicionales) — error detectado y corregido durante esta verificación.

---

## 0. Prerrequisitos

- [ ] Máquina 2 en la intranet con IP fija y salida a Internet (Alpha Vantage/Twelve Data e IB Gateway).
      *Salida a Internet verificada 06/10/2026 (llamadas reales a Twelve Data); IP fija pendiente de confirmar en el router.*
- [x] Docker con plugin `docker compose` v2 instalado. *(verificado 06/10/2026: 29.8.2 / compose 5.6.0)*
- [x] `uv` instalado (`curl -LsSf https://astral.sh/uv/install.sh | sh`). *(verificado)*
- [x] Repositorio clonado: `git clone <url> ~/ritech && cd ~/ritech`.

## 1. Código y dependencias

```bash
uv venv --python 3.12
uv pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

- [x] **Criterio:** todas las pruebas en verde (≥53) y Python 3.12.x.
      *(verificado 06/10/2026: `53 passed`, Python 3.12.14)*

## 2. Credenciales en reposo (RNF-006)

- [x] Copiar `.env` y `.env.local` desde la estación de desarrollo (permisos `600`).
      `.env` lleva los secretos cifrados; `.env.local` la `CREDENTIALS_MASTER_KEY`.
      *(verificado 06/10/2026: presentes con permisos 600)*
- [ ] En producción la clave maestra NO debe vivir junto a los secretos: inyectarla
      como `EnvironmentFile` del servicio del SO (fuera de `.env`, root-only).
- [ ] Verificar el ciclo completo de cifrado con la key real:

```bash
set -a; source .env; source .env.local; set +a
.venv/bin/python -m src.security.crypto decrypt "$(grep '^TWELVE_DATA_API_KEY_ENC=' .env | cut -d= -f2)"
```

- [x] **Criterio:** imprime la API key en claro (enmascarar al compartir evidencia).
      *(verificado 06/10/2026: roundtrip AES-256-GCM OK, máscara `c18c...1b4d`)*
- [x] **Criterio:** `grep -R "apikey\|API_KEY" .env` no muestra ninguna clave en claro.
      *(verificado 06/10/2026: `.env` sin key en claro)*

## 3. Infraestructura de datos (ítem 4)

```bash
docker compose up -d          # PostgreSQL 16 + InfluxDB 2.7
docker compose ps             # ambos deben quedar "healthy"
docker compose exec postgres pg_isready -U ritech -d ritech
curl -f http://127.0.0.1:8086/health
```

- [x] **Criterio:** los dos contenedores `healthy` y ambos comandos responden OK.
      *(verificado 06/10/2026: `ritech-postgres16` y `ritech-influxdb27` → `healthy`;
      `pg_isready` → "accepting connections"; `/health` → `pass` v2.7.12)*
- [x] **Criterio:** puertos solo en loopback `127.0.0.1` (REST-05): `ss -tlnp | grep -E '5432|8086'` no muestra `0.0.0.0`.
      *(verificado 06/10/2026: solo `127.0.0.1:5432` y `127.0.0.1:8086`)*
- [ ] Detener (los volúmenes conservan datos): `docker compose down`.

## 4. Orquestador end-to-end (ítems 5 + 6 + 9)

```bash
set -a; source .env; source .env.local; set +a
.venv/bin/python -m src.ingestion.scheduler
```

- [x] **Criterio:** log `INFO ... Vela <epoch_ms> persistida · variación ... · rendimiento ...`
      cada ~300 s, al menos 3 ciclos consecutivos (RF-101).
      *(verificado 17/10/2026 — 3 ciclos reales contra Twelve Data + InfluxDB
      con la guarda de CR-003 activa: ciclo 1→2 en **300.6 s**, ciclo 2→3 en
      **300.6 s** — desviación **+0.6 s** sobre el objetivo de 300 s en ambos.
      Cronómetro completo en `/tmp/opencode/scheduler_cadence.log`.
      Ejecutado un sábado: los tres ciclos devolvieron la misma vela, por lo que
      la guarda de inmutabilidad omitió la escritura en los ciclos 2 y 3 con
      `WARNING Escritura omitida: la vela con timestamp ... ya existe`, lo que
      además re-valida CR-003 en condiciones reales.)*
- [ ] **Criterio:** una falla de red o de validación produce `WARNING`/`ERROR`,
      **no** termina el proceso; el siguiente ciclo se ejecuta igual.
      *(camino de error cubierto por la suite en `tests/test_scheduler.py`;
      la verificación en vivo de una caída de red sigue pendiente)*
- [x] **Criterio (RF-102):** payload rechazado por validación → mensaje
      "Payload de la fuente de datos rechazado..." y **cero** escrituras en InfluxDB.
      *(verificado 06/10/2026 con errores reales de la API: `403 prepost` y `404 NDX`
      normalizados a `{"error": ...}` → `validate_payload` False → `run_once`
      retorna antes de `write_to_influx`; cubierto también por la suite)*

## 5. Verificación de datos en InfluxDB (RF-103)

```bash
curl --request POST "http://127.0.0.1:8086/api/v2/query?org=ritech" \
  --header "Authorization: Token $INFLUXDB_ADMIN_TOKEN" \
  --header "Content-Type: application/flux" \
  --data 'from(bucket:"nasdaq_ticks") |> range(start: -2h)
          |> filter(fn: (r) => r._measurement == "NasdaqTick") |> limit(n: 5)'
```

- [x] **Criterio:** aparecen puntos `NasdaqTick` con `timestamp` epoch ms y campos
      `price_open`, `price_close`, `volume`.
      *(verificado 06/10/2026 — lectura de vuelta desde Flux tras escritura real:
      `timestamp=1791316500000` (2026-10-06 15:55 ET), `price_open=759.51001`,
      `price_close=759.60999`, `volume=1736118.0` — valores idénticos a la API)*

  **Inmutabilidad — verificación empírica (06/10/2026), no asumida:** InfluxDB
  **no** es append-only a nivel de punto. Reescribir el mismo timestamp con un
  `volume` alterado NO lanzó error ni creó un duplicado: quedó **1 punto** con
  el valor nuevo (*last write wins* / overwrite silencioso); se restauró el
  valor original por la misma vía y se verificó la vuelta a `1736118.0`.
  → **Cerrado con CR-003 (cierre del Sprint 2):** la inmutabilidad del histórico
  (RF-103) ahora la garantiza la capa de aplicación mediante una guarda
  *insert-if-absent* en `write_to_influx()` — si el timestamp ya existe, **no
  escribe** y emite `WARNING`. Verificado de nuevo contra InfluxDB real: la
  vela `1791316500000` se intentó reescribir con `price_open=999,
  price_close=888, volume=777` → `WARNING Escritura omitida…` y lectura de
  vuelta con los valores originales intactos
  (`759.51001 / 759.60999 / 1736118.0`). Detalle y limitación declarada
  (read-then-write) en `docs/change-requests.md` CR-003.

## 6. Pendientes conocidos al cerrar Sprint 2 (no son fallas de M2)

- [ ] **CR-002 — cambio de fuente (registrada en `docs/change-requests.md`, pendiente
      de aprobación formal):** Alpha Vantage → Twelve Data (verificado
      06/10/2026: intraday/índices premium, cuota 25/día < 192 requeridas). Uso del
      plan gratuito *Basic* es **académico/personal**; producción real exige plan
      comercial licenciado. Límites Basic verificados en vivo: solo horario regular
      09:30–16:00 ET (78 velas de las ~192 del escenario RF-101; `prepost` exige
      plan Pro+) y NDX directo exige plan Grow+ (se usa QQQ como proxy).
- [ ] **CR-001 — `price_high`/`price_low` (registrada en `docs/change-requests.md`,
      pendiente de aprobación formal):** inconsistencia alto/bajo entre SRS y firma
      SDD `write_to_influx` (marcado `TODO(SRS-4.2.2)` en el código).
      *Impacto Sprint 3:* bloquea el uso de `price_high`/`price_low` como features
      del modelo predictivo (RF-301); la primera versión del modelo trabaja con
      apertura, cierre y volumen, que son los tres campos que la firma aprobada
      sí persiste.
- [ ] **CR-003 — inmutabilidad del histórico (implementada y verificada, pendiente
      de aprobación formal):** ver §5 de este checklist y `docs/change-requests.md`.
- [ ] **Deuda `TODO(RF-204)`:** ventanas de exclusión (registrada en `docs/backlog.md`).
- [ ] **RNF-003 — RECLASIFICADO a "no verificable aún"** (ver §7): la medición del
      Sprint 2 no cubre el alcance del requisito (faltan IA y UI, e incluye el
      fetch externo). Se re-mide al cierre de Sprint 3.
- [ ] FastAPI y Dashboard entran en Sprint 3 (decisión registrada en
      `src/calculator/asymmetric.py`).

## 7. Hallazgos de la verificación en vivo (06/10/2026)

### RNF-003 — ciclo real fetch→validate→write→calculate (RECLASIFICADO: no verificable aún)

> **Reclasificación al cierre del Sprint 2.** Lo que sigue es la medición
> original del 06/10/2026. Se mantiene íntegra como evidencia, pero **ya no
> se interpreta como incumplimiento**: RNF-003 (SRS §6.2) mide *"procesar una
> nueva vela de 5 minutos, consultar InfluxDB, ejecutar el modelo de IA local,
> calcular la asimetría y actualizar la interfaz gráfica" **"en el servidor
> local"***. El ciclo medido **incluye el fetch externo** (fuera del alcance
> del requisito) y **carece de dos de sus cinco componentes** — la inferencia
> de IA y la actualización de UI, ambos en Sprint 3. El estado correcto es
> **no verificable aún**; se re-mide al cierre de Sprint 3 excluyendo el fetch
> del cronómetro y reportando por separado la porción local. Ver
> `docs/backlog.md`.

Medido con `perf_counter` sobre `run_once()` real (Twelve Data + InfluxDB local,
3 iteraciones consecutivas):

| Muestra | fetch (ext.) | validate | write | calculate | **wall total** |
|---|---:|---:|---:|---:|---:|
| 1 | 1032.9 ms | 10.8 ms | 9.3 ms | ~0 ms | **1116.1 ms** |
| 2 | 613.7 ms | 5.0 ms | 54.0 ms | ~0 ms | **677.5 ms** |
| 3 | 523.3 ms | 1.4 ms | 12.1 ms | ~0 ms | **540.0 ms** |

- **Análisis:** el 89–95% del tiempo es el HTTP externo a Twelve Data (RTT de
  red, fuera del servidor local). El procesamiento local (validate + write +
  calculate) quedó en **6–59 ms**.
- **Alcance del requisito:** RNF-003 (SRS §6.2 Eficiencia de Desempeño) dice
  "…no debe superar los 500 ms **en el servidor local**" e incluye además el
  modelo de IA y la actualización
  de UI, que aún no existen (Sprint 3). Con el alcance del Sprint 2 la porción
  local ya cabe, pero el ciclo completo mediido tal cual excede 500 ms en 3/3
  muestras.
- **Estado:** hallazgo registrado, **sin optimizar** (decisión del equipo:
  estrategias posibles — caché del último candle, tolerar el fetch externo como
  parte de RF-101 y no de RNF-003, o exigir RTT al proveedor).
- **Estado tras el cierre del Sprint 2:** reclasificado a **"no verificable
  aún"** (ver recuadro al inicio de §7). No se optimizó nada: se espera a tener
  los dos componentes faltantes para medir el alcance real del requisito.

### Otros hallazgos

- **Bug de entorno `source .env .env.local`:** el patrón de un solo `source`
  con dos archivos solo carga el primero (el segundo se vuelve argumento
  posicional). Detectado al lanzar el orquestador en vivo (`CryptoError:
  Falta CREDENTIALS_MASTER_KEY`); corregido en todos los comandos de este
  checklist a `source .env; source .env.local`.
- **Inmutabilidad de InfluxDB:** ver hallazgo empírico en §5 (overwrite
  silencioso). **Cerrado con CR-003** mediante guarda *insert-if-absent* en la
  capa de aplicación, verificado contra InfluxDB real.
