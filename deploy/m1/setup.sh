#!/usr/bin/env bash
# RiTech-NASDAQ100 — instalación en la Máquina 1 (Nodo IA)
#
# Uso (desde el repo, como root o con sudo):
#     sudo deploy/m1/setup.sh
#
# Es idempotente: se puede volver a correr sin miedo. No arranca nada ni
# habilita los timers automáticos — eso es deliberado, y se hace al final con
# los comandos que imprime, para que la primera puesta en marcha sea visible.
#
# Qué instala
# -----------
#   - el usuario del sistema `ritech` (sin shell, sin home interactivo)
#   - el entorno virtual con los grupos `ai` y `ai-cuda` (torch con CUDA,
#     fastapi, uvicorn)
#   - las unidades systemd de inferencia y de reentrenamiento
#   - el archivo de entorno /etc/ritech/m1.env desde la plantilla, si no existe
#
# Qué NO hace
# -----------
#   - No arranca el timer de reentrenamiento automático.
#   - No rellena credenciales: eso lo hace una persona.
#   - No copia el repo: se ejecuta desde él.

set -euo pipefail

# Raíz del repo: la que contiene este script, dos niveces arriba.
RAIZ="$(cd "$(dirname "$0")/../.." && pwd)"
UNIDADES="$RAIZ/deploy/m1"

PYTHON_MINIMO="3.12"
ENTORNO_ETC="/etc/ritech/m1.env"
USUARIO="ritech"
MODELOS="${MODEL_PATH:-$RAIZ/models}"

log()   { printf '\n\033[1m== %s ==\033[0m\n' "$*" >&2; }
paso()  { printf '  · %s\n' "$*" >&2; }
aviso() { printf '  ¡ ATENCIÓN: %s\n' "$*" >&2; }

[ "$(id -u)" -eq 0 ] || {
    echo "Este script necesita root (instala unidades en /etc/systemd/system)." >&2
    echo "Relanzar con: sudo $0" >&2
    exit 1
}

# ---------------------------------------------------------------------------
log "1/6 · Usuario del sistema"
# ---------------------------------------------------------------------------
if id "$USUARIO" >/dev/null 2>&1; then
    paso "el usuario $USUARIO ya existe"
else
    # `--system` crea una cuenta de servicio. Sin shell y sin home interactivo:
    # esta cuenta no sirve para entrar al equipo.
    useradd --system --home-dir "$RAIZ" --shell /usr/sbin/nologin \
            --comment "RiTech NASDAQ-100" "$USUARIO"
    paso "creado el usuario $USUARIO (sin shell)"
fi

# ---------------------------------------------------------------------------
log "2/6 · Entorno virtual"
# ---------------------------------------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
    aviso 'no se encontró uv. Instalarlo antes de continuar:'
    echo "      curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
    exit 1
fi

cd "$RAIZ"
paso "creando el entorno virtual con Python $PYTHON_MINIMO…"
uv venv .venv --python "$PYTHON_MINIMO"

# `--no-dev --group ai --group ai-cuda`: esta máquina necesita el servicio de
# inferencia (fastapi, uvicorn) y la variante de torch **con CUDA**, y no el
# grupo de desarrollo. `--group ai-cuda` es lo que elige el índice cu126; sin
# él no se resuelve torch desde ningún sitio, y el paso siguiente lo acusa.
# Ver `pyproject.toml` y `docs/despliegue.md` §3.
paso 'instalando dependencias de los grupos ai y ai-cuda…'
uv sync --no-dev --group ai --group ai-cuda

paso "comprobando que torch ve la GPU…"
if ! "$RAIZ/.venv/bin/python" -c 'import torch; assert torch.cuda.is_available(), "sin CUDA"' \
        >/dev/null 2>&1; then
    aviso "torch NO ve la GPU. El servicio correrá en CPU."
    echo "      Revisar el driver NVIDIA y que la versión de torch sea la de CUDA." >&2
    echo "      Detalle:" >&2
    "$RAIZ/.venv/bin/python" -c 'import torch; print("        torch", torch.__version__, "| CUDA", torch.version.cuda, "| disponible", torch.cuda.is_available())' >&2 || true
fi

# ---------------------------------------------------------------------------
log "3/6 · Directorio de modelos"
# ---------------------------------------------------------------------------
mkdir -p "$MODELOS"
chown -R "$USUARIO:$USUARIO" "$MODELOS"
paso "$MODELOS (propiedad de $USUARIO)"

# ---------------------------------------------------------------------------
log "4/6 · Archivo de entorno"
# ---------------------------------------------------------------------------
if [ -f "$ENTORNO_ETC" ]; then
    paso "$ENTORNO_ETC ya existe: no se toca"
else
    install -m 600 -o "$USUARIO" -g "$USUARIO" \
            "$RAIZ/.env.m1.example" "$ENTORNO_ETC"
    aviso "$ENTORNO_ETC creado desde la plantilla y SIN RELLENAR."
    echo "      Editarlo antes de arrancar:  $ENTORNO_ETC" >&2
fi
paso "permisos $(stat -c '%a %U:%G' "$ENTORNO_ETC" 2>/dev/null || echo 600)"

# ---------------------------------------------------------------------------
log "5/6 · Unidades systemd"
# ---------------------------------------------------------------------------
for unidad in ritech-inference.service ritech-retrain.service ritech-retrain.timer; do
    install -m 644 "$UNIDADES/$unidad" "/etc/systemd/system/$unidad"
    paso "$unidad"
done
systemctl daemon-reload

# ---------------------------------------------------------------------------
log "6/6 · Hecho"
# ---------------------------------------------------------------------------
cat >&2 <<TXT

Instalación completa. Faltan tres pasos y ninguno es automático a propósito.

  1. Rellenar el archivo de entorno (mínimo privilegio: M1 NO recibe la API
     key de Twelve Data, ni credenciales de PostgreSQL, ni las de IB):

         sudo -u $USUARIO nano $ENTORNO_ETC

     En concreto: INFERENCE_HOST con la IP interna de esta máquina si se va a
     servir a M2, e INFLUXDB_TOKEN con un token de SOLO LECTURA acotado al
     bucket nasdaq_ticks.

  2. Entrenar el primer modelo. Sin él no hay nada que servir, y el servicio
     fallará al arrancar, que es lo que corresponde:

         sudo -u $USUARIO $RAIZ/.venv/bin/python -m src.ai.train

  3. Arrancar y comprobar:

         sudo systemctl enable --now ritech-inference.service
         $RAIZ/deploy/m1/verify.sh

El reentrenamiento diario NO está activado todavía. Cuando el primer modelo
esté sirviendo y se haya decidido que sí:

         sudo systemctl enable --now ritech-retrain.timer

Ver el calendario del timer:  systemctl list-timers ritech-retrain.timer
Ver la inferencia:            journalctl -u ritech-inference.service -f
TXT
