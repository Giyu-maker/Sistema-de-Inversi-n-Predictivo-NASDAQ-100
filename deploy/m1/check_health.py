#!/usr/bin/env python
"""Comprueba que el servicio de inferencia responde en `/health`.

Lo llaman `deploy/m1/promote.sh` (después de reiniciar la inferencia con el
modelo nuevo) y `deploy/m1/verify.sh`. Existe como archivo aparte, y no como
un bloque de Python dentro de un heredoc de shell, por la misma razón que
`check_artifact.py`: el cuerpo lleva paréntesis, que dentro de una
sustitución `$( )` de bash se confunden con el cierre de la sustitución.

Salida y código de salida
-------------------------
* Éxito (0): una línea en stdout, con campos separados por `|`:

      model_type|feature_schema_version|dispositivo|ventana|n_features|artefacto

* Fallo (1): el motivo en stderr.

El tope de espera existe porque el servicio tarda lo que tarde en cargar el
`state_dict` en la GPU — no responde nada hasta que el modelo está dentro.
Sin espera, una verificación recién después de un reinicio reportaría un
falso negativo.

Las variables `INFERENCE_HOST` e `INFERENCE_PORT` se leen del entorno y no de
los argumentos: son exactamente las mismas con las que arranca el servicio, de
modo que aquí se comprueba la dirección pública real, no una que haya que
recordar escribir.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import httpx

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = "8000"
CAMPOS = (
    "model_type",
    "feature_schema_version",
    "dispositivo",
    "ventana",
    "n_features",
    "artefacto",
)


def url_del_servicio() -> str:
    """URL de `/health` a partir del entorno."""
    host = os.environ.get("INFERENCE_HOST", DEFAULT_HOST)
    puerto = os.environ.get("INFERENCE_PORT", DEFAULT_PORT)
    return f"http://{host}:{puerto}/health"


def consultar(url: str, timeout_s: float) -> tuple[dict[str, object] | None, str]:
    """Devuelve `(cuerpo, "")` si responde, o `(None, motivo)` si no."""
    try:
        with httpx.Client(timeout=timeout_s) as cliente:
            respuesta = cliente.get(url)
    except httpx.HTTPError as exc:
        return None, str(exc)

    if respuesta.status_code != 200:
        cuerpo = respuesta.text[:300]
        return None, f"HTTP {respuesta.status_code}: {cuerpo}"

    try:
        datos = respuesta.json()
    except ValueError as exc:
        return None, f"/health devolvió algo que no es JSON: {exc}"
    if not isinstance(datos, dict):
        return None, f"/health devolvió {type(datos).__name__}, no un objeto"
    return datos, ""


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Comprueba que el servicio de inferencia responde en /health.",
    )
    parser.add_argument(
        "--tope-s",
        type=float,
        default=1.0,
        help="segundos máximos de espera antes de rendirse (por defecto 1: una sola consulta)",
    )
    parser.add_argument(
        "--intervalo-s",
        type=float,
        default=2.0,
        help="segundos entre intentos mientras se espera",
    )
    args = parser.parse_args(argv[1:])

    if args.tope_s < 0:
        print("--tope-s no puede ser negativo", file=sys.stderr)
        return 2

    url = url_del_servicio()
    limite = time.monotonic() + args.tope_s
    ultimo_error = ""
    ultimo_intento = time.monotonic()

    while True:
        ultimo_intento = time.monotonic()
        cuerpo, ultimo_error = consultar(url, timeout_s=min(args.tope_s, 5.0) or 5.0)
        if cuerpo is not None:
            faltantes = [c for c in CAMPOS if c not in cuerpo]
            if faltantes:
                print(
                    f"/health no trae los campos que M2 espera: {faltantes}",
                    file=sys.stderr,
                )
                return 1
            print("|".join(str(cuerpo[c]) for c in CAMPOS))
            return 0

        restante = limite - ultimo_intento
        if restante <= 0:
            break
        time.sleep(min(args.intervalo_s, restante))

    print(
        f"/health no respondió en {args.tope_s:g}s ({url}). Último estado: {ultimo_error}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
