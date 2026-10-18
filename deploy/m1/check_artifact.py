#!/usr/bin/env python
"""Comprueba que el artefacto al que apunta `active.json` se puede cargar.

Lo llaman `deploy/m1/promote.sh` y `deploy/m1/verify.sh`. Existe como archivo
aparte, y no como un bloque de Python incrustado en un heredoc de shell, por
dos razones: el cuerpo contiene paréntesis, que en un `$( )` de bash se
confunden con el cierre de la sustitución, y porque una comprobación que se
ejecuta dos veces no debe estar escrita dos veces.

La regla con la que se resuelve el puntero **no está aquí**: está en
`src.ai.model.ruta_desde_puntero`, que es quien la comparte con
`src.ai.train` (que la escribe) y con `src.ai.serve` (que la lee al arrancar).
Que esté en un solo sitio es lo que evita que quien escribe y quien lee vuelvan
a no ponerse de acuerdo.

Salida y código de salida
-------------------------
* Éxito (0): `ruta|model_type|feature_schema_version`.
* Fallo (1): el motivo en stderr.
* Mal uso (2): la línea de uso.

Se carga con `map_location="cpu"` a propósito: lo que se comprueba es que el
archivo esté bien formado y sea portable, no que la GPU esté disponible en
este instante. El servicio ya la usará al arrancar.
"""

from __future__ import annotations

import pathlib
import sys

# Permite ejecutar el archivo directamente sin instalar el paquete.
_RAIZ = pathlib.Path(__file__).resolve().parents[2]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))


def main(argv: list[str]) -> int:
    """`argv` son solo los argumentos, sin el nombre del programa."""
    if len(argv) != 1:
        print("uso: check_artifact.py RUTA/A/active.json", file=sys.stderr)
        return 2

    # El import va dentro: así la línea de uso sigue funcionando aunque falte
    # torch, que es lo que más conviene ver primero en una máquina recién
    # instalada.
    from src.ai.model import load_artifact, ruta_desde_puntero

    puntero = pathlib.Path(argv[0])
    try:
        ruta = ruta_desde_puntero(puntero)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    try:
        artefacto = load_artifact(str(ruta), map_location="cpu")
    except Exception as exc:  # noqa: BLE001 — el motivo es el mensaje, no el tipo
        print(f"el artefacto {ruta} no se pudo cargar: {exc}", file=sys.stderr)
        return 1

    print(
        f"{ruta}|{artefacto['model_type']}|{artefacto['feature_schema_version']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
