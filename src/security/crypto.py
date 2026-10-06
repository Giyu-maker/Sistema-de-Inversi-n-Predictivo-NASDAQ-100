"""Cifrado de secretos en reposo con AES-256-GCM (RNF-006).

Las credenciales de APIs externas (Twelve Data, Alpha Vantage e
Interactive Brokers) nunca se guardan en
texto plano: se cifran con `encrypt_secret` y el token resultante es lo único
que se almacena (p. ej. en `.env`, que no se commitea).

La clave maestra se toma de la variable de entorno `CREDENTIALS_MASTER_KEY`
(32 bytes en hexadecimal). Uso desde la línea de comandos:

    export CREDENTIALS_MASTER_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')"
    python -m src.security.crypto encrypt "valor-en-claro"   # imprime el token
    python -m src.security.crypto decrypt "token"            # imprime el valor en claro
"""

from __future__ import annotations

import base64
import os
import sys

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_ENV_VAR = "CREDENTIALS_MASTER_KEY"
_KEY_SIZE_BYTES = 32  # AES-256
_NONCE_SIZE_BYTES = 12  # 96 bits, recomendado para GCM


class CryptoError(Exception):
    """Clave maestra faltante/mal formada o token corrupto."""


def _load_key() -> bytes:
    """Lee y valida la clave maestra (hex de 32 bytes) desde el entorno."""
    raw = os.environ.get(KEY_ENV_VAR, "").strip()
    if not raw:
        raise CryptoError(
            f"Falta la variable de entorno {KEY_ENV_VAR} (clave hex de {_KEY_SIZE_BYTES * 2} caracteres)."
        )
    try:
        key = bytes.fromhex(raw)
    except ValueError as exc:
        raise CryptoError(f"{KEY_ENV_VAR} no es hexadecimal válido.") from exc
    if len(key) != _KEY_SIZE_BYTES:
        raise CryptoError(
            f"{KEY_ENV_VAR} debe tener {_KEY_SIZE_BYTES} bytes ({_KEY_SIZE_BYTES * 2} caracteres hex)."
        )
    return key


def encrypt_secret(plaintext: str) -> str:
    """Cifra un secreto en claro y retorna un token base64-url listo para almacenar."""
    if not plaintext:
        raise CryptoError("El secreto a cifrar no puede estar vacío.")
    nonce = os.urandom(_NONCE_SIZE_BYTES)
    ciphertext = AESGCM(_load_key()).encrypt(nonce, plaintext.encode("utf-8"), None)
    return base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")


def decrypt_secret(token: str) -> str:
    """Descifra un token generado por `encrypt_secret`.

    Lanza `CryptoError` si el token está corrupto o si la clave maestra
    no corresponde a la usada al cifrar (integridad GCM).
    """
    if not token:
        raise CryptoError("El token a descifrar no puede estar vacío.")
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise CryptoError("El token no es base64-url válido.") from exc
    if len(raw) <= _NONCE_SIZE_BYTES:
        raise CryptoError("Token demasiado corto: no contiene nonce + ciphertext.")
    nonce, ciphertext = raw[:_NONCE_SIZE_BYTES], raw[_NONCE_SIZE_BYTES:]
    try:
        plaintext = AESGCM(_load_key()).decrypt(nonce, ciphertext, None)
    except InvalidTag as exc:
        raise CryptoError("Descifrado fallido: token corrupto o clave maestra incorrecta.") from exc
    return plaintext.decode("utf-8")


def _main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in {"encrypt", "decrypt"}:
        print('Uso: python -m src.security.crypto {encrypt|decrypt} "valor"', file=sys.stderr)
        return 2
    try:
        result = encrypt_secret(argv[1]) if argv[0] == "encrypt" else decrypt_secret(argv[1])
    except CryptoError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
