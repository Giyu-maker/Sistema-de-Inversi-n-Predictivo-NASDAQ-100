"""Clave de reinicio del Kill-Switch — la única autorización que queda (CR-006).

Por qué una clave y no un usuario
---------------------------------
El alcance del Sprint 4 se recortó: no hay login, ni roles, ni TOTP (brecha
declarada en CR-006). Lo que sí se conserva es que **reiniciar** —es decir,
volver a dejar operable un sistema que se detuvo a sí mismo— no puede ser una
acción anónima. Por eso el reinicio exige una clave cuyo **hash** vive en el
entorno (`KILL_SWITCH_RESET_KEY_HASH`) y nunca en el repositorio.

* **Disparar** el Kill-Switch sigue siendo anónimo (RF-501): en una
  emergencia, pedir credenciales mata a la persona.
* **Reiniciar** exige la clave, con comparación en tiempo constante y límite
  de intentos persistido en `kill_switch_state`.

Formato del hash
----------------
`pbkdf2-sha256$<iteraciones>$<sal hex>$<hash hex>`, todo generado con la
stdlib: no hace falta ninguna dependencia nueva para esto, y las
iteraciones viajan dentro de la cadena para poder subirlas sin tocar código.

Generarlo
---------
    .venv/bin/python -m src.security.clave_reinicio

Pide la clave dos veces sin ecoarla (nunca por argumento: quedaría en el
historial del shell) y devuelve la línea completa para pegarla en `.env`.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from typing import Final

logger = logging.getLogger(__name__)

#: Nombre de la variable de entorno que guarda el hash.
VARIABLE: Final = "KILL_SWITCH_RESET_KEY_HASH"

#: PBKDF2-HMAC-SHA256 con 310 000 iteraciones (recomendación OWASP 2023).
#: Con una clave de alta entropía el costo de la fuerza bruta es despreciable
#: de todos modos, y el retardo por intento es un efecto secundario útil.
ITERACIONES: Final = 310_000
SAL_BYTES: Final = 16
ALGORITMO: Final = "pbkdf2-sha256"


def generar_hash(clave: str, *, iteraciones: int = ITERACIONES) -> str:
    """Hash listo para `KILL_SWITCH_RESET_KEY_HASH` (sal aleatoria nueva)."""
    if not clave:
        raise ValueError("la clave de reinicio no puede estar vacía")
    sal = secrets.token_bytes(SAL_BYTES)
    digesto = hashlib.pbkdf2_hmac(
        "sha256", clave.encode("utf-8"), sal, iteraciones
    )
    return f"{ALGORITMO}${iteraciones}${sal.hex()}${digesto.hex()}"


def verificar(clave: str, guardado: str | None) -> bool:
    """¿Coincide la clave con el hash guardado? Comparación en tiempo constante.

    Todo fallo devuelve `False`, nunca lanza: una clave mal formada en el
    entorno no puede tumbar el endpoint ni —mucho menos— dejar el reinicio
    abierto. El error queda en el log del servidor.
    """
    if not clave or not guardado:
        return False
    try:
        algoritmo, iteraciones, sal_hex, hash_hex = guardado.split("$", 3)
        if algoritmo != ALGORITMO:
            raise ValueError(f"algoritmo desconocido: {algoritmo}")
        sal = bytes.fromhex(sal_hex)
        esperado = bytes.fromhex(hash_hex)
        iteraciones_n = int(iteraciones)
        if iteraciones_n <= 0:
            raise ValueError("iteraciones no positivas")
    except (ValueError, TypeError) as exc:
        # No se informa al caller del motivo: es un problema de configuración
        # del servidor, y su mensaje no le sirve a quien intenta la clave.
        logger.error("KILL_SWITCH_RESET_KEY_HASH mal formado: %s", exc)
        return False

    obtenido = hashlib.pbkdf2_hmac(
        "sha256", clave.encode("utf-8"), sal, iteraciones_n
    )
    return hmac.compare_digest(obtenido, esperado)


def desde_entorno() -> str | None:
    """Hash configurado, o `None` si nadie lo puso (reinicio deshabilitado)."""
    valor = os.environ.get(VARIABLE, "").strip()
    return valor or None


def main(argv: list[str] | None = None) -> int:
    """CLI: genera el hash leyendo la clave sin eco (nunca en el historial)."""
    import argparse
    import getpass
    import sys

    parser = argparse.ArgumentParser(
        description="Genera el valor de KILL_SWITCH_RESET_KEY_HASH para .env"
    )
    parser.add_argument(
        "--iteraciones", type=int, default=ITERACIONES,
        help=f"iteraciones de PBKDF2 (por defecto {ITERACIONES})",
    )
    args = parser.parse_args(argv)

    primera = getpass.getpass("Clave de reinicio: ")
    if not primera:
        print("Clave vacía: no se generó nada.", file=sys.stderr)
        return 1
    segunda = getpass.getpass("Repita la clave de reinicio: ")
    if primera != segunda:
        print("Las claves no coinciden.", file=sys.stderr)
        return 1

    print()
    print("# Pegar en el .env del servicio (gitignored), no en el repo:")
    print(f"{VARIABLE}={generar_hash(primera, iteraciones=args.iteraciones)}")
    return 0


if __name__ == "__main__":  # pragma: no cover — CLI manual
    raise SystemExit(main())
