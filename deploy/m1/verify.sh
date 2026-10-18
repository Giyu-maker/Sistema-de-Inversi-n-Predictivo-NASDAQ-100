#!/usr/bin/env bash
# RiTech-NASDAQ100 — verificación de la Máquina 1 (Nodo IA)
#
# Uso:
#     deploy/m1/verify.sh
#
# Comprueba que la instalación está completa y que la inferencia responde.
# Sale con código distinto de cero si algo falla, para poder encadenarlo:
#
#     deploy/m1/setup.sh && deploy/m1/verify.sh
#
# Además comprueba algo que no es obvio: que el archivo de entorno de esta
# máquina NO contenga secretos que no le corresponden. Copiar por error el
# `.env` de la Máquina 2 en la Máquina 1 es fácil, y daría a un nodo que solo
# debe predecir las llaves del broker y de la base de datos.

set -uo pipefail

RAIZ="$(cd "$(dirname "$0")/../.." && pwd)"
USUARIO="ritech"
ENTORNO_ETC="${RITECH_ENV_FILE:-/etc/ritech/m1.env}"
PYTHON="$RAIZ/.venv/bin/python"
MODELOS="${MODEL_PATH:-$RAIZ/models}"

# Se trabaja desde la raíz: de aquí salen las rutas relativas de los tests y
# `src` tiene que poder importarse para comprobar el artefacto.
cd "$RAIZ"

FALLOS=0
TOTAL=0

ok()   { TOTAL=$((TOTAL + 1)); printf '  \033[32m✓\033[0m %s\n' "$*" >&2; }
mal()  { TOTAL=$((TOTAL + 1)); FALLOS=$((FALLOS + 1)); printf '  \033[31m✗\033[0m %s\n' "$*" >&2; }
nota() { printf '      %s\n' "$*" >&2; }
seccion() { printf '\n\033[1m%s\033[0m\n' "$*" >&2; }

# ---------------------------------------------------------------------------
seccion "Entorno de ejecución"
# ---------------------------------------------------------------------------
if [ -x "$PYTHON" ]; then
    ok "intérprete: $("$PYTHON" -V 2>&1)"
else
    mal "no existe $PYTHON — correr deploy/m1/setup.sh"
fi

if [ -x "$PYTHON" ] && "$PYTHON" -c 'import torch' >/dev/null 2>&1; then
    ok "torch instalado: $("$PYTHON" -c 'import torch; print(torch.__version__)' 2>&1)"
    if "$PYTHON" -c 'import torch; raise SystemExit(0 if torch.cuda.is_available() else 1)' >/dev/null 2>&1; then
        ok "CUDA disponible: $("$PYTHON" -c 'import torch; print(torch.cuda.get_device_name(0))' 2>&1)"
    else
        mal "torch NO ve la GPU: la inferencia correrá en CPU"
        nota "torch reparte las librerías de CUDA con la rueda; en el host solo"
        nota "hace falta el driver de NVIDIA y haber sincronizado con el índice"
        nota "de CUDA: uv sync --no-dev --group ai --group ai-cuda"
        nota "si aun así no la ve, revisar nvidia-smi y la versión del driver"
    fi
else
    mal "torch no está instalado — correr deploy/m1/setup.sh"
fi

if [ -x "$PYTHON" ] && "$PYTHON" -c 'import fastapi' >/dev/null 2>&1; then
    ok "fastapi instalado"
else
    mal 'falta fastapi (grupo ai)'
fi

# ---------------------------------------------------------------------------
seccion "Mínimo privilegio (§15.2)"
# ---------------------------------------------------------------------------
# Estas variables son de la Máquina 2. Si estuvieran aquí, este nodo tendría
# credenciales que no necesita, y eso es un fallo de configuración, no un
# detalle.
PROHIBIDAS="TWELVE_DATA_API_KEY TWELVE_DATA_API_KEY_ENC POSTGRES_PASSWORD POSTGRES_USER IB_API_KEY IB_API_KEY_ENC IB_ACCOUNT_ID IB_ACCOUNT_ID_ENC CREDENTIALS_MASTER_KEY"
if [ ! -f "$ENTORNO_ETC" ]; then
    mal "no existe $ENTORNO_ETC"
else
    ENCONTRADAS=""
    for variable in $PROHIBIDAS; do
        if grep -Eq "^[[:space:]]*${variable}=[[:space:]]*[^[:space:]#]+" "$ENTORNO_ETC"; then
            ENCONTRADAS="$ENCONTRADAS $variable"
        fi
    done
    if [ -n "$ENCONTRADAS" ]; then
        mal "el archivo de entorno de M1 tiene secretos que no le corresponden:$ENCONTRADAS"
        nota "parece un .env de la Máquina 2 copiado por error"
    else
        ok "$ENTORNO_ETC sin secretos de otras máquinas"
    fi

    PERMISOS="$(stat -c '%a' "$ENTORNO_ETC" 2>/dev/null || echo '???')"
    if [ "$PERMISOS" = "600" ]; then
        ok "permisos del entorno: 600"
    else
        mal "permisos del entorno: $PERMISOS (debe ser 600) — chmod 600 $ENTORNO_ETC"
    fi
fi

# ---------------------------------------------------------------------------
seccion "Modelo"
# ---------------------------------------------------------------------------
PUNTERO="$MODELOS"
if [ -f "$PUNTERO" ]; then
    # `MODEL_PATH` apunta al archivo suelto: el puntero está en su directorio.
    PUNTERO="$(dirname "$MODELOS")/active.json"
else
    # Apunta al directorio: el puntero es `active.json` dentro de él.
    PUNTERO="$MODELOS/active.json"
fi

if [ -f "$PUNTERO" ]; then
    ok "hay un puntero activo: $PUNTERO"
    if [ -x "$PYTHON" ]; then
        if SALIDA="$("$PYTHON" deploy/m1/check_artifact.py "$PUNTERO" 2>&1)"; then
            ok "el artefacto se carga: $(printf '%s' "$SALIDA" | cut -d'|' -f2), esquema $(printf '%s' "$SALIDA" | cut -d'|' -f3)"
            nota "$(printf '%s' "$SALIDA" | cut -d'|' -f1)"
        else
            mal "el artefacto NO se puede cargar"
            nota "$SALIDA"
        fi
    fi
else
    mal "no hay $PUNTERO — entrenar con: $PYTHON -m src.ai.train"
fi

# ---------------------------------------------------------------------------
seccion "Unidades systemd"
# ---------------------------------------------------------------------------
for unidad in ritech-inference.service ritech-retrain.service ritech-retrain.timer; do
    if systemctl cat "$unidad" >/dev/null 2>&1; then
        ok "$unidad instalada"
    else
        mal "$unidad no está instalada"
    fi
done

if systemctl is-active --quiet ritech-inference.service; then
    ok "ritech-inference.service en ejecución"
else
    mal "ritech-inference.service NO está en ejecución"
    nota "systemctl status ritech-inference.service"
fi

if systemctl is-enabled --quiet ritech-retrain.timer 2>/dev/null; then
    ok "ritech-retrain.timer habilitado"
    nota "próximo reentrenamiento: $(systemctl list-timers ritech-retrain.timer --no-legend 2>/dev/null | awk '{print $1, $2, $3, $4, $5}')"
else
    nota "ritech-retrain.timer NO está habilitado (correcto hasta que haya un modelo sirviendo)"
fi

# ---------------------------------------------------------------------------
seccion "Servicio de inferencia"
# ---------------------------------------------------------------------------
HOST="${INFERENCE_HOST:-127.0.0.1}"
PUERTO="${INFERENCE_PORT:-8000}"

S_ESQUEMA=""
if [ -x "$PYTHON" ]; then
    # Un solo contacto con el servicio; ya se sabe que está respondiendo o no.
    # La comprobación está en `check_health.py`, no en un heredoc, porque el
    # cuerpo lleva paréntesis que dentro de un `$( )` de bash se confunden con
    # el cierre de la sustitución.
    if SALIDA="$("$PYTHON" deploy/m1/check_health.py --tope-s 5 2>&1)"; then
        IFS='|' read -r S_TIPO S_ESQUEMA S_DISPOSITIVO S_VENTANA S_FEATURES S_ARTEFACTO \
            <<< "$SALIDA"
        ok "/health responde en http://$HOST:$PUERTO"
        nota "$S_TIPO | esquema $S_ESQUEMA | $S_DISPOSITIVO | ventana ${S_VENTANA}x${S_FEATURES} | artefacto $S_ARTEFACTO"
    else
        mal "/health NO responde en http://$HOST:$PUERTO"
        nota "$SALIDA"
    fi

    # La versión de esquema que sirve M1 debe coincidir con la del código que
    # corre aquí. Si no, M2 rechazaría cada predicción — mejor enterarse ahora
    # que en la primera petición que llegue en horario de operación.
    if [ -n "$S_ESQUEMA" ] && [ -f "$PUNTERO" ]; then
        CODIGO_ESQUEMA="$("$PYTHON" -c 'from src.ai.features import FEATURE_SCHEMA_VERSION as v; print(v)' 2>/dev/null || echo '?')"
        if [ "$CODIGO_ESQUEMA" = "$S_ESQUEMA" ] && [ "$CODIGO_ESQUEMA" != "?" ]; then
            ok "el esquema del código y el del servicio coinciden ($CODIGO_ESQUEMA)"
        else
            mal "esquemas distintos: código $CODIGO_ESQUEMA, servicio $S_ESQUEMA"
            nota "el servicio está sirviendo un modelo entrenado con otro esquema de features"
        fi
    fi
else
    mal "no hay intérprete con el que comprobar /health"
fi

# ---------------------------------------------------------------------------
seccion "Resultado"
# ---------------------------------------------------------------------------
if [ "$FALLOS" -eq 0 ]; then
    printf '  \033[32m%d comprobaciones, todas correctas.\033[0m\n\n' "$TOTAL" >&2
    exit 0
fi
printf '  \033[31m%d de %d comprobaciones fallaron.\033[0m\n\n' "$FALLOS" "$TOTAL" >&2
exit 1
