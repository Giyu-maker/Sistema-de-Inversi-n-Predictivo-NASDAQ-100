#!/usr/bin/env bash
# Operación de la sesión completa sin intervención (prioridad lunes 26/10).
#
#   scripts/sesion.sh start    — inferencia en 2.º plano (log + PID), espera
#                                /health y después `docker compose up -d`.
#   scripts/sesion.sh stop     — apaga la inferencia y el stack de Docker.
#   scripts/sesion.sh estado   — velas de hoy, última vela, última predicción
#                                y errores recientes de los logs.
#
# El servicio de inferencia escucha en la IP del puente de Docker (docker0),
# NUNCA en 0.0.0.0: es la interfaz por la que los contenedores —incluidos los
# de `network_mode: host`— llegan a él sin abrir el servicio a toda la red
# (REST-05: residencia y mínimo privilegio). El valor se vuelca en
# `.env.sesion` (gitignored, opcional en compose), que los servicios `api` y
# `scheduler` leen DESPUÉS de `.env.local`, así el script manda sin tocar los
# archivos del usuario.
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"

PID_FILE="/tmp/ritech-inferencia.pid"
LOG_FILE="/tmp/ritech-inferencia.log"
PUERTO="${INFERENCE_PORT:-8000}"

puente() {
  ip -4 addr show docker0 2>/dev/null | awk '/inet /{split($2, a, "/"); print a[1]}'
}

vivo() {
  [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

inferencia_salud() {
  curl -fsS -m 2 "http://${1}:${PUERTO}/health" >/dev/null 2>&1
}

api_salud() {
  curl -fsS -m 2 "http://127.0.0.1:8010/api/health" >/dev/null 2>&1
}

cargar_entorno() {
  set -a
  # shellcheck disable=SC1091
  [ -f .env ] && source .env
  # shellcheck disable=SC1091
  [ -f .env.local ] && source .env.local
  set +a
}

arrancar() {
  local brillo
  brillo="$(puente)"
  if [ -z "$brillo" ]; then {
    echo "No existe la interfaz docker0 (¿Docker corriendo?)." >&2
    exit 1
  }; fi

  if vivo && inferencia_salud "$brillo"; then
    echo "Inferencia ya corriendo (PID $(cat "$PID_FILE")) en ${brillo}:${PUERTO}."
  else
    [ -f "$PID_FILE" ] && rm -f "$PID_FILE"  # PID viejo: limpiar rastro
    cargar_entorno
    echo "Arrancando inferencia en ${brillo}:${PUERTO} (log: ${LOG_FILE})…"
    nohup env INFERENCE_HOST="$brillo" INFERENCE_PORT="$PUERTO" \
      .venv/bin/python -m src.ai.serve >>"$LOG_FILE" 2>&1 &
    echo $! >"$PID_FILE"
    local intento
    for intento in $(seq 1 60); do
      if inferencia_salud "$brillo"; then break; fi
      if ! vivo; then
        echo "La inferencia terminó antes de responder. Últimas líneas del log:" >&2
        tail -n 20 "$LOG_FILE" >&2
        exit 1
      fi
      sleep 1
    done
    inferencia_salud "$brillo" || {
      echo "La inferencia no respondió /health en 60 s. Revisar ${LOG_FILE}." >&2
      exit 1
    }
    echo "Inferencia OK (PID $(cat "$PID_FILE"))."
  fi

  # Los contenedores consumen INFERENCE_URL desde este volcado (tiene
  # prioridad sobre .env.local por el orden del env_file en compose).
  printf 'INFERENCE_URL=http://%s:%s\n' "$brillo" "$PUERTO" >.env.sesion

  docker compose up -d

  local intento
  for intento in $(seq 1 30); do
    api_salud && break
    sleep 1
  done
  api_salud || { echo "El API no respondió /api/health en 30 s." >&2; exit 1; }

  echo
  echo "Stack listo: inferencia + Docker. Estado:"
  docker compose ps
}

parar() {
  if vivo; then
    kill "$(cat "$PID_FILE")" 2>/dev/null || true
    sleep 1
    echo "Inferencia detenida (PID $(cat "$PID_FILE"))."
    rm -f "$PID_FILE"
  else
    echo "La inferencia no estaba corriendo."
  fi
  rm -f .env.sesion
  docker compose down
  echo "Stack apagado."
}

estado() {
  local brillo hoy
  brillo="$(puente)"
  hoy="$(date +%d/%m/%Y)"
  echo "=== Inferencia ==="
  if vivo && [ -n "$brillo" ] && inferencia_salud "$brillo"; then
    echo "corriendo (PID $(cat "$PID_FILE")) — http://${brillo}:${PUERTO}/health OK"
  elif [ -n "$brillo" ] && inferencia_salud "$brillo"; then
    echo "responde en ${brillo}:${PUERTO} (PID file ausente o viejo)"
  else
    echo "DETENIDA o sin responder en ${brillo:-<sin docker0>}:${PUERTO}"
  fi

  echo
  echo "=== API (127.0.0.1:8010) ==="
  if api_salud; then
    echo "salud: OK"
    echo
    echo "--- Velas (últimas 200; hoy $hoy) ---"
    curl -fsS -m 5 "http://127.0.0.1:8010/api/candles?limit=200" \
      | .venv/bin/python -c '
import json, sys
d = json.load(sys.stdin)
velas = d.get("velas", [])
hoy = sys.argv[1]
n = sum(1 for v in velas if str(v.get("fecha", "")).startswith(hoy))
print("velas de hoy: %d" % n)
u = velas[-1] if velas else None
if u:
    print("última vela: %s @ %s" % (u["fecha"], u["close"]))
else:
    print("última vela: —")
' "$hoy"
    echo
    echo "--- Última predicción ---"
    curl -fsS -m 5 "http://127.0.0.1:8010/api/predictions/latest" \
      | .venv/bin/python -c '
import json, sys
d = json.load(sys.stdin)
p = d.get("prediccion")
if p:
    print("%s — %s con %.2f %%" % (p["fecha"], p["trend_prediction"], float(p["confidence_score"]) * 100))
else:
    print("sin predicciones registradas")
'
  else
    echo "DETENIDA o sin responder"
  fi

  echo
  echo "=== Errores recientes ==="
  if [ -f "$LOG_FILE" ]; then
    echo "--- inferencia (${LOG_FILE}) ---"
    grep -iE "error|exception|traceback" "$LOG_FILE" | tail -n 10 || echo "(sin coincidencias)"
  else
    echo "(sin log de inferencia)"
  fi
  echo "--- scheduler (contenedor, últimas 2 h) ---"
  docker compose logs --since 2h scheduler 2>/dev/null \
    | grep -iE "error|exception|traceback" | tail -n 5 || echo "(sin coincidencias)"
}

case "${1:-}" in
  start)  arrancar ;;
  stop)   parar ;;
  estado) estado ;;
  *)
    echo "Uso: scripts/sesion.sh start|stop|estado" >&2
    exit 2
    ;;
esac
