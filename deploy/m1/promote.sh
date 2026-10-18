#!/usr/bin/env bash
# RiTech-NASDAQ100 — promoción segura del modelo reentrenado (Máquina 1)
#
# Lo hace `ritech-retrain.service`. También se puede disparar a mano:
#     systemctl start ritech-retrain.service
#     deploy/m1/promote.sh
#
# Por qué existe
# --------------
# `active.json` es un puntero compartido entre dos procesos: lo escribe el
# entrenamiento y lo lee el servicio de inferencia. Sin comprobación, un
# artefacto que no cargara dejaba la inferencia caída hasta que alguien la
# mirara — y ese es justo el peor momento para descubrirlo: en horario de
# operación, con el sistema ciego.
#
# Qué hace, en orden
# ------------------
#  1. Guarda el puntero `active.json` actual.
#  2. Entrena. Eso escribe el artefacto nuevo y mueve el puntero.
#  3. Comprueba que el artefacto al que apunta el puntero **se puede cargar**.
#  4. Reinicia el servicio de inferencia y comprueba que `/health` responde.
#  5. Si algo de lo anterior falla, restaura el puntero anterior, reinicia de
#     nuevo y sale con error, para que el timer quede en `failed` y el fallo
#     se pueda ver con `systemctl status`.
#
# Todo lo que puede fallar termina en el puntero anterior. Un reentrenamiento
# malo deja el modelo de ayer sirviendo, que es infinitamente mejor que no
# tener modelo.

set -euo pipefail

PYTHON="${RITECH_PYTHON:-/opt/ritech/.venv/bin/python}"
SERVICIO="${RITECH_INFERENCE_UNIT:-ritech-inference.service}"
MODELOS="${MODEL_PATH:-/opt/ritech/models}"

log()    { printf '[promote] %s\n' "$*" >&2; }
_cago()  { printf '[promote] ERROR: %s\n' "$*" >&2; exit 1; }

cd "$(dirname "$0")/../.."

[ -x "$PYTHON" ] || _cago "no existe el intérprete $PYTHON"

# `MODEL_PATH` puede apuntar al archivo suelto o al directorio; en los dos
# casos el puntero vive en el directorio.
if [ -f "$MODELOS" ]; then
    MODELOS="$(dirname "$MODELOS")"
fi
[ -d "$MODELOS" ] || _cago "no existe el directorio de modelos: $MODELOS"

PUNTERO="$MODELOS/active.json"
[ -f "$PUNTERO" ] || _cago "no hay $PUNTERO: todavía no se ha entrenado ningún modelo"

ANTES="$(mktemp)"
trap 'rm -f "$ANTES"' EXIT
cp "$PUNTERO" "$ANTES"
log "puntero antes: $(tr -d '[:space:]' < "$ANTES")"

# --- 1. entrenar -------------------------------------------------------------
log "entrenando (esto tarda; ver journalctl -u ritech-retrain.service)…"
if ! "$PYTHON" -m src.ai.train; then
    cp "$ANTES" "$PUNTERO"
    _cago "el entrenamiento falló; el puntero anterior se restauró"
fi

# --- 2. comprobar que el artefacto nuevo carga -------------------------------
# La comprobación vive en `check_artifact.py` y no en un heredoc: el cuerpo
# lleva paréntesis, que dentro de una sustitución `$( )` de bash se confunden
# con el cierre. Carga con `map_location="cpu"` a propósito: lo que se
# comprueba es que el archivo esté bien formado y sea portable, no que la GPU
# esté disponible ahora mismo. El servicio ya la usará al arrancar.
if ! RUTA_NUEVA="$("$PYTHON" deploy/m1/check_artifact.py "$PUNTERO")"; then
    cp "$ANTES" "$PUNTERO"
    _cago "el artefacto nuevo no se pudo cargar; el puntero anterior se restauró"
fi

RUTA="$(printf '%s' "$RUTA_NUEVA" | cut -d'|' -f1)"
TIPO="$(printf '%s' "$RUTA_NUEVA" | cut -d'|' -f2)"
ESQUEMA="$(printf '%s' "$RUTA_NUEVA" | cut -d'|' -f3)"
log "artefacto nuevo: $RUTA ($TIPO, esquema $ESQUEMA)"

# --- 3. reiniciar la inferencia y comprobar que responde ---------------------
log "reiniciando $SERVICIO…"
systemctl restart "$SERVICIO"

# /health tarda lo que tarde en cargar el state_dict en la GPU. Se espera con
# tope; si se acabara el tope, el servicio sigue caído y hay que revertir.
# La comprobación está en `check_health.py` por la misma razón que la de
# arriba: este bloque lleva paréntesis, que dentro de un `$( )` de bash se
# confunden con el cierre de la sustitución.
ESPERA_MAX_S="${RITECH_HEALTH_TIMEOUT_S:-90}"
if ! SALUD="$("$PYTHON" deploy/m1/check_health.py --tope-s "$ESPERA_MAX_S" 2>&1)"; then
    cp "$ANTES" "$PUNTERO"
    log "la inferencia no arrancó con el modelo nuevo; se restaura el puntero anterior"
    log "motivo: $SALUD"
    systemctl restart "$SERVICIO" || true
    _cago "la inferencia quedó caída con el modelo nuevo; se restauró el de antes"
fi
log "la inferencia responde: $SALUD"

log "promoción completa: $RUTA"
