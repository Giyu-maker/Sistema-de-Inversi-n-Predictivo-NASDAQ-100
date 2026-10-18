# Despliegue — RiTech-NASDAQ100

Manual de instalación y operación del sistema en el clúster de intranet. Cubre el
estado del **Sprint 3, Fase A**: modelo predictivo local en la Máquina 1, su
servicio de inferencia y el cliente que lo consume desde la Máquina 2.

> **Qué es este documento y qué no.**
> Este es el manual de **instalación y operación** que exige `AGENTS.md` §15
> («Instrucciones exactas en `docs/despliegue.md`»).
> **No es el runbook RF-801.** RF-801 (SRS §4.1) es un entregable distinto: el
> manual de usuario para el operador y la secretaria con la secuencia exacta de
> mitigación ante fallos en los sistemas de comunicación con Interactive
> Brokers. Ese todavía no existe y corresponde al **Sprint 4** (Ítem 12).
> Los dos documentos no se confunden: este lo lee quien instala el sistema;
> RF-801 lo lee quien opera con él cuando algo se cae.

---

## 1. Qué corre dónde

```
Twelve Data ──▶ IngestionEngine ──▶ InfluxDB ──▶ LocalPredictiveModel ──▶ RiskManager ──▶ IB Gateway
   (API)            M2               M2            M1 (CUDA)               M2             M2
                                                              ▲
                                                              │ HTTP /predict
                                                         cliente M2
```

| | Máquina 1 — Nodo IA | Máquina 2 — Nodo Central | Máquina 3 — Operador |
|---|---|---|---|
| Sistema | CachyOS x86_64 | CachyOS x86_64 | Windows 11 |
| GPU | GTX 1660 SUPER | integrada | integrada |
| Software | `src.ai.train`, `src.ai.serve` | Docker (PostgreSQL 16, InfluxDB 2.7), ingesta, API | Dashboard React |
| Grupos uv | `ai` + `ai-cuda` | `ai` + `ai-cpu` + `dev` | ninguno (usa Node) |

Dos reglas de las que no se aparta nada de lo que sigue, ambas de `AGENTS.md` §15:

1. **M1 no escribe en PostgreSQL.** Las predicciones las persiste M2 en
   `ai_predictions`. M1 solo entrena y responde por HTTP.
2. **Mínimo privilegio.** Cada máquina recibe solo los secretos que necesita.
   M1 **no** recibe la API key de Twelve Data, ni credenciales de PostgreSQL,
   ni las de Interactive Brokers, ni la clave maestra de criptografía.

---

## 2. Requisitos previos

| | M1 | M2 |
|---|---|---|
| Python | 3.12 (lo pone `uv venv`) | 3.12 |
| `uv` | imprescindible | imprescindible |
| Docker + Compose | no | sí (los contenedores) |
| Driver NVIDIA | sí (`nvidia-smi` debe responder) | no |
| Red | alcanzable desde M2 y alcanzando M2 | — |

Instalar `uv` si no está:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

---

## 3. Dependencias — un lockfile, dos variantes de torch

`uv.lock` está versionado y es la fuente de verdad de las versiones. **Es único
para las dos máquinas**: `uv` lo resuelve de forma universal y registra las tres
ruedas de `torch 2.9.1` que el proyecto puede necesitar (`+cpu`, `+cu126` y la
de macOS), cada una atada a su grupo.

Eso se consigue con dos declaraciones en `pyproject.toml`:

- **`[tool.uv] conflicts`** — los grupos `ai-cpu` y `ai-cuda` son excluyentes.
  Sin eso, `uv` rechazaría el lockfile: no cabe una misma plataforma con dos
  ruedas distintas para el mismo paquete y versión.
- **`[tool.uv.sources]`** — de qué índice sale `torch` según el grupo pedido,
  con `explicit = true` para que ningún otro paquete se resuelva sin querer
  contra el índice de torch.

### Comandos exactos

**Máquina 1 — con GPU** (instala `torch==2.9.1+cu126`):

```bash
cd /opt/ritech
uv venv .venv --python 3.12
uv sync --no-dev --group ai --group ai-cuda
```

**Máquina 2 — desarrollo y pruebas** (instala `torch==2.9.1+cpu`):

```bash
cd /ruta/al/repo
uv sync --group ai --group ai-cpu
```

**Máquina 3 — no necesita Python.** El dashboard es Node/React.

### Por qué cu126 y no cu128 ni cu130

`torch` reparte las librerías de CUDA dentro de la propia rueda: en la Máquina 1
**solo hace falta el driver de NVIDIA**, no el toolkit. Entre cu126, cu128 y
cu130, cu126 es el que exige el driver más antiguo, así que es el que menos
probabilidades tiene de fallar en un equipo cuyo estado del driver no se conoce
todavía. La GTX 1660 SUPER (arquitectura Turing, `sm_75`) es compatible.

Si al instalar se comprueba que la Máquina 1 tiene un driver reciente, subir de
índice es cambiar una URL en `pyproject.toml` y volver a bloquear:

```bash
# en [[tool.uv.index]] con name = "pytorch-cuda"
url = "https://download.pytorch.org/whl/cu128"   # o cu130
uv lock
uv sync --no-dev --group ai --group ai-cuda
```

### Si algo no cuadra con el lockfile

`uv sync` es autoritario: cualquier instalación hecha a mano con
`uv pip install` o `pip install` se deshace en la siguiente sincronización. Eso
es intencionado. Si una máquina necesita algo que no está en el lockfile, se
declara en `pyproject.toml` y se vuelve a bloquear; no se parchea el venv.

---

## 4. Variables de entorno

Hay una plantilla por máquina, y **no son intercambiables**:

| Archivo | Máquina | Contiene |
|---|---|---|
| `.env.m1.example` | M1 | `MODEL_PATH`, `INFERENCE_*`, `MODEL_ARCH`, InfluxDB de **solo lectura** |
| `.env.m2.example` | M2 | Twelve Data, PostgreSQL, InfluxDB, IB, `INFERENCE_URL` |
| `.env.m3.example` | M3 | prácticamente nada (es deliberado) |

Instalación:

```bash
sudo install -m 600 -o ritech -g ritech .env.m1.example /etc/ritech/m1.env
sudo nano /etc/ritech/m1.env          # rellenar a mano
```

`.env` y `.env.local` están en `.gitignore`; **nunca se commitea** un archivo
rellenado. Las plantillas sí viajan en el repositorio porque están vacías.

### Mínimo privilegio, comprobado

`deploy/m1/verify.sh` no se limita a mirar que exista el archivo: comprueba que
**no** contenga `TWELVE_DATA_API_KEY*`, `POSTGRES_*`, `IB_*` ni
`CREDENTIALS_MASTER_KEY`. Copiar por error el `.env` de M2 en M1 es un fallo
fácil de cometer y daría a un nodo que solo debe predecir las llaves del broker
y de la base de datos. El script lo señala como error, no como aviso.

### Las tres variables que suelen cambiar entre entornos

| Variable | M1 | M2 |
|---|---|---|
| `MODEL_PATH` | `/opt/ritech/models` (directorio) | no aplica |
| `INFERENCE_HOST` / `INFERENCE_PORT` | IP interna de M1 / `8000` | — |
| `INFERENCE_URL` | — | `http://<IP-de-M1>:8000` |

Nada de esto está escrito en el código (`§15.1`): cambiar de entorno no
requiere tocar código ni reconstruir.

---

## 5. InfluxDB de M2 a M1 — paso por la red, opt-in

Para entrenar, M1 necesita **leer** las velas que ingiere M2. Por defecto
`INFLUXDB_URL=http://127.0.0.1:8086` y, como M1 no tiene InfluxDB, esa URL no
sirve: el paso por la intranet se activa **expresamente**, editando la variable.

Antes de abrirlo:

1. **Token de solo lectura y acotado al bucket.** En M2:

   ```bash
   influx auth create --org ritech --read-bucket nasdaq_ticks
   ```

   El resultado va a `INFLUXDB_TOKEN` de M1. El `INFLUXDB_ADMIN_TOKEN` de M2
   **no se copia**: es el token de administración y da de más.
2. **Publicar el contenedor en la red interna.** En M2 el contenedor está
   ligado a `127.0.0.1` (verificado en el cierre del Sprint 2); llegar desde
   M1 exige cambiar esa publicación a la IP interna de M2. Es una decisión de
   configuración, no una consecuencia de instalar esto.
3. **Publicar solo en la interfaz de la intranet**, nunca en `0.0.0.0`.

> **Limitación de seguridad conocida (RNF-005).** InfluxDB 2.7 se expone aquí
> por HTTP sin TLS. El tráfico viaja por la red local de la oficina, sin salir
> de Colombia ni de la intranet (REST-01, REST-05), y el token viaja en claro
> dentro de esa red. Se documenta en lugar de ocultarlo: endurecerlo con TLS o
> con un túnel es trabajo pendiente, y debe decidirse antes de dar el sistema
> por conectado en producción. **Pendiente de aprobación del arquitecto.**

Si no se activa, M1 no puede entrenar con datos de M2 y solo servirá el modelo
que ya tenga cargado — que es un estado válido para arrancar y comprobar, pero
no para el reentrenamiento diario.

---

## 6. Instalar la Máquina 1

```bash
git clone <repo> /opt/ritech       # o copiar el árbol ya versionado
cd /opt/ritech
sudo deploy/m1/setup.sh
```

`setup.sh` es idempotente y hace seis cosas:

1. Crea el usuario de sistema `ritech` (sin shell, sin home interactivo).
2. Crea el venv con Python 3.12 y sincroniza `--no-dev --group ai --group ai-cuda`.
3. Comprueba si `torch` ve la GPU y, si no, lo dice con el detalle.
4. Crea `models/` con propiedad de `ritech`.
5. Instala `/etc/ritech/m1.env` desde la plantilla **si no existe** (no lo pisa).
6. Instala las tres unidades systemd y recarga el demonio.

**No arranca nada.** Es deliberado: el primer arranque se ve, no se pierde en un
script que además imprime «todo bien». El propio script imprime los tres pasos
que faltan.

### Unidades instaladas

| Unidad | Tipo | Qué hace |
|---|---|---|
| `ritech-inference.service` | daemon | `python -m src.ai.serve`; `/health` y `/predict` |
| `ritech-retrain.service` | `oneshot` | llama a `deploy/m1/promote.sh` |
| `ritech-retrain.timer` | timer | dispara el anterior cada día a las 20:30 (America/Bogota) |

---

## 7. Primer modelo y arranque

Sin modelo no hay nada que servir, y el servicio falla al arrancar. Es el
comportamiento correcto y no conviene maquillarlo.

```bash
# 1. Primer entrenamiento (tarda; hace falta INFLUXDB_TOKEN si se va a leer de M2)
sudo -u ritech /opt/ritech/.venv/bin/python -m src.ai.train

# 2. Arrancar
sudo systemctl enable --now ritech-inference.service

# 3. Comprobar todo
/opt/ritech/deploy/m1/verify.sh
```

`verify.sh` comprueba el intérprete, torch y la GPU, el **mínimo privilegio** del
archivo de entorno, que el artefacto se carga, las unidades, el estado del
servicio, que `/health` responde y que **la versión de esquema que sirve coincide
con la del código**. Sale con código distinto de cero si algo falla.

### El contrato del artefacto

- `MODEL_PATH` acepta **el archivo `.pt` suelto** o **el directorio que contiene
  `active.json`**.
- Con el directorio, `src.ai.train` deja tras cada reentrenamiento un modelo
  nuevo apuntado por `active.json`, y basta reiniciar el servicio: no hay que
  reconfigurar nada. Por eso `MODEL_PATH` apunta al directorio en producción.
- Las rutas dentro de `active.json` son **relativas al propio puntero**. La
  convención está en `src.ai.model.ruta_desde_puntero`, que es quien la
  comparte con quien escribe (`train`) y con quien lee (`serve` y
  `check_artifact.py`).

### Cambiar de arquitectura sin reentrenar

Cada corrida entrena y guarda **las dos** (`mlp` y `linear`); `MODEL_ARCH`
decide cuál puntea `active.json`:

```bash
MODEL_ARCH=linear   # en /etc/ritech/m1.env
sudo systemctl restart ritech-inference.service
```

El default es `mlp`, **no por rendimiento**: en el Sprint 3 el lineal igualó o
superó al MLP y la diferencia no alcanzó significancia estadística. Se sirve el
MLP porque el SRS §5.3 y el SDD §5.3.2 exigen una red neuronal. Ver
`docs/informe-entrenamiento-sprint3.md` §10.

---

## 8. Reentrenamiento diario

RF-302 pide reentrenamiento automático al cierre de la jornada. No se añade
planificador alguno al sistema: se monta con lo que ya está en CachyOS.

```bash
# Solo después de que el primer modelo esté sirviendo y se haya decidido que sí:
sudo systemctl enable --now ritech-retrain.timer

systemctl list-timers ritech-retrain.timer     # próxima corrida
journalctl -u ritech-retrain.service           # última corrida
systemctl start ritech-retrain.service         # dispararlo a mano
```

### Por qué a las 20:30 de Colombia

La línea del timer es `OnCalendar=*-*-* 20:30:00 America/Bogota` y **no** una
hora del servidor. Colombia no tiene horario de verano; el Este de EE. UU. sí.
Con la zona del servidor, la misma línea habría significado 20:30 en invierno y
19:30 en verano, y en verano eso caería **antes** de la hora mínima que fija el
SRS (8:00 PM hora de Nueva York).

| Estación | 20:30 CO = | Cierre del mercado | Cumple SRS |
|---|---|---|---|
| verano (EDT) | 21:30 ET | 16:00 ET (15:00 CO) | sí, 5,5 h después |
| invierno (EST) | 20:30 ET | 16:00 ET (16:00 CO) | sí, 4,5 h después |

Se usa tarde a propósito: el dataset del día solo está completo cuando InfluxDB
ya tiene las velas de toda la sesión. Apurar la hora ganaría minutos a cambio
de entrenar con un día incompleto.

`Persistent=true` hace que, si la Máquina 1 estuvo apagada a esa hora, al
encender ejecute la corrida omitida. Sin eso, un equipo apagado desde el
viernes se quedaría con el modelo del viernes en silencio.

### `promote.sh` — por qué el reentrenamiento no puede ser ciego

`active.json` es un puntero compartido entre dos procesos: lo escribe el
entrenamiento y lo lee el servicio. Sin comprobación, un artefacto que no
cargara dejaría la inferencia caída hasta que alguien la mirara — y ese es
justo el peor momento para descubrirlo.

```
1. Guarda active.json actual
2. Entrena (escribe el artefacto nuevo y mueve el puntero)
3. Comprueba que el artefacto nuevo SE PUEDE CARGAR
4. Reinicia la inferencia y espera a que /health responda
5. Si algo falla: restaura el puntero anterior, reinicia de nuevo,
   y sale con error para que el timer quede en `failed`
```

Un reentrenamiento malo deja el modelo de ayer sirviendo, que es infinitamente
mejor que no tener modelo.

---

## 9. Operación cotidiana

```bash
# Estado
systemctl status ritech-inference.service
deploy/m1/verify.sh

# Logs
journalctl -u ritech-inference.service -f        # en vivo
journalctl -u ritech-retrain.service -f
journalctl -u ritech-retrain.service --since today

# Parar / arrancar
sudo systemctl stop ritech-inference.service
sudo systemctl start ritech-inference.service

# Ver qué modelo está sirviendo, sin mirar el archivo
curl -s http://127.0.0.1:8000/health | python -m json.tool
```

`/health` no devuelve solo `"ok"`: declara `model_type`,
`feature_schema_version`, `dispositivo`, `ventana`, `n_features` y `artefacto`.
Es lo que permite saber desde fuera **qué** se está sirviendo.

### Cómo se prueban las piezas por separado

```bash
deploy/m1/check_artifact.py models/active.json   # ¿el artefacto carga?
deploy/m1/check_health.py --tope-s 5             # ¿el servicio responde?
```

Las dos están fuera del código (`§15.4`) y son exactamente las que llaman
`promote.sh` y `verify.sh`; no hay lógica de comprobación escrita dos veces.

---

## 10. Fallos que ya se conocen

### 10.1 El puntero `active.json` (corregido en el Sprint 3)

**Síntoma:** el servicio no arranca con
`el puntero /opt/ritech/models/active.json apunta a .../models/models/x.pt, que no existe`.

**Causa:** `src.ai.train` escribía la ruta relativa a la **raíz del proyecto**
(`models/x.pt`) y `src.ai.serve` la resolvía contra el directorio del puntero,
con lo que construía `models/models/x.pt`.

**Por qué no se vio en desarrollo:** `MODEL_PATH` apuntaba al `.pt` suelto, así
que esa rama no se ejecutaba. Aparece al poner `MODEL_PATH` al directorio, que
es como corre en la Máquina 1. La clase de fallo que solo existe en el sitio.

**Estado:** corregido. La convención vive ahora en un solo sitio
(`src.ai.model.ruta_desde_puntero`), escritor y lector la comparten, y hay
tres pruebas de regresión, incluida una que **cambia el directorio de trabajo**
y vuelve a resolver el puntero — que es lo que fallaba.

**Qué hacer si aparece en un equipo desplegado con una versión anterior:**
reentrenar (`python -m src.ai.train`), que vuelve a escribir el puntero bien.
El mensaje de error ya lo dice.

### 10.2 Errores de validación que devolvían 500 en vez de 422 (corregido)

`json.loads` de Python acepta `Infinity`, que JSON no. Una ventana con
`Infinity` llegaba a la validación, y el manejador por defecto de FastAPI
devolvía el cuerpo recibido **dentro del error**; esa respuesta contenía
`Infinity` y no se podía serializar, con lo que el cliente veía un 500
en lugar del 422 esperado.

Corregido con un manejador propio (`_manejar_validacion`) que devuelve
`{"detail": [{"campo", "mensaje", "tipo"}]}` sin eco del cuerpo, y con una
prueba de regresión.

### 10.3 Pendiente hasta correr en la Máquina 1

Marcado en `docs/backlog.md` como **no verificado**, no como incumplido:

- Que `torch` encuentre la GPU de la GTX 1660 SUPER con el índice cu126.
- Que las unidades systemd arranquen con `ProtectSystem=strict` y
  `ReadWritePaths` correctos.
- Que el timer dispare a la hora prevista y `promote.sh` complete el ciclo con
  rollback.
- El rendimiento del ciclo completo (RNF-003), que se re-mide al cierre del
  Sprint 3.

---

## 11. Qué queda fuera (Fase B y Sprint 4)

No forma parte de este despliegue:

- La API de M2 para el dashboard, el dashboard y la integración con la UI.
- El Kill-switch, la auditoría, las notificaciones y la integración con
  Interactive Brokers.
- **RF-801**: el runbook de mitigación de fallos de IB para el operador.
  Es el documento que este manual **no** es; ver el encabezado.
- TLS para InfluxDB en la intranet (§5).

---

## 12. Referencias

- `AGENTS.md` §15 — modelo de desarrollo y despliegue (fuente de las reglas que
  este documento aplica).
- `docs/srs.md` — requisitos funcionales y no funcionales.
- `docs/informe-entrenamiento-sprint3.md` — métricas, límites y decisiones.
- `docs/backlog.md` — estado de cada ítem y lo que queda pendiente.
- `deploy/m1/` — scripts y unidades que este manual invoca.
