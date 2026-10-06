# Checklist de verificación — Máquina 2 (Nodo Central)

Procedimiento de despliegue y verificación del Walking Skeleton (Sprint 2) en la
Máquina 2. La máquina de desarrollo **no tiene Docker**: nada de este checklist
se ejecuta allí. Cada punto tiene un criterio de aceptación explícito.

---

## 0. Prerrequisitos

- [ ] Máquina 2 en la intranet con IP fija y salida a Internet (Alpha Vantage/Twelve Data e IB Gateway).
- [ ] Docker con plugin `docker compose` v2 instalado.
- [ ] `uv` instalado (`curl -LsSf https://astral.sh/uv/install.sh | sh`).
- [ ] Repositorio clonado: `git clone <url> ~/ritech && cd ~/ritech`.

## 1. Código y dependencias

```bash
uv venv --python 3.12
uv pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

- [ ] **Criterio:** todas las pruebas en verde (≥53) y Python 3.12.x.

## 2. Credenciales en reposo (RNF-006)

- [ ] Copiar `.env` y `.env.local` desde la estación de desarrollo (permisos `600`).
      `.env` lleva los secretos cifrados; `.env.local` la `CREDENTIALS_MASTER_KEY`.
- [ ] En producción la clave maestra NO debe vivir junto a los secretos: inyectarla
      como `EnvironmentFile` del servicio del SO (fuera de `.env`, root-only).
- [ ] Verificar el ciclo completo de cifrado con la key real:

```bash
set -a; source .env .env.local; set +a
.venv/bin/python -m src.security.crypto decrypt "$(grep '^TWELVE_DATA_API_KEY_ENC=' .env | cut -d= -f2)"
```

- [ ] **Criterio:** imprime la API key en claro (enmascarar al compartir evidencia).
- [ ] **Criterio:** `grep -R "apikey\|API_KEY" .env` no muestra ninguna clave en claro.

## 3. Infraestructura de datos (ítem 4)

```bash
docker compose up -d          # PostgreSQL 16 + InfluxDB 2.7
docker compose ps             # ambos deben quedar "healthy"
docker compose exec postgres pg_isready -U ritech -d ritech
curl -f http://127.0.0.1:8086/health
```

- [ ] **Criterio:** los dos contenedores `healthy` y ambos comandos responden OK.
- [ ] **Criterio:** puertos solo en loopback `127.0.0.1` (REST-05): `ss -tlnp | grep -E '5432|8086'` no muestra `0.0.0.0`.
- [ ] Detener (los volúmenes conservan datos): `docker compose down`.

## 4. Orquestador end-to-end (ítems 5 + 6 + 9)

```bash
set -a; source .env .env.local; set +a
.venv/bin/python -m src.ingestion.scheduler
```

- [ ] **Criterio:** log `INFO ... Vela <epoch_ms> persistida · variación ... · rendimiento ...`
      cada ~300 s, al menos 3 ciclos consecutivos (RF-101).
- [ ] **Criterio:** una falla de red o de validación produce `WARNING`/`ERROR`,
      **no** termina el proceso; el siguiente ciclo se ejecuta igual.
- [ ] **Criterio (RF-102):** payload rechazado por validación → mensaje
      "Payload de la fuente de datos rechazado..." y **cero** escrituras en InfluxDB.

## 5. Verificación de datos en InfluxDB (RF-103)

```bash
curl --request POST "http://127.0.0.1:8086/api/v2/query?org=ritech" \
  --header "Authorization: Token $INFLUXDB_ADMIN_TOKEN" \
  --header "Content-Type: application/flux" \
  --data 'from(bucket:"nasdaq_ticks") |> range(start: -2h)
          |> filter(fn: (r) => r._measurement == "NasdaqTick") |> limit(n: 5)'
```

- [ ] **Criterio:** aparecen puntos `NasdaqTick` con `timestamp` epoch ms y campos
      `price_open`, `price_close`, `volume` (sin escrituras destructivas sobre histórico).

## 6. Pendientes conocidos al cerrar Sprint 2 (no son fallas de M2)

- [ ] **CR pendiente — cambio de fuente:** Alpha Vantage → Twelve Data (verificado
      06/10/2026: intraday/índices premium, cuota 25/día < 192 requeridas). Uso del
      plan gratuito *Basic* es **académico/personal**; producción real exige plan
      comercial licenciado.
- [ ] **CR pendiente — `TODO(SRS-4.2.2)`:** inconsistencia alto/bajo entre SRS y firma
      SDD `write_to_influx`.
- [ ] **Deuda `TODO(RF-204)`:** ventanas de exclusión (registrada en `docs/backlog.md`).
- [ ] FastAPI y Dashboard entran en Sprint 3 (decisión registrada en
      `src/calculator/asymmetric.py`).
