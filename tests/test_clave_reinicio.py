"""Clave de reinicio: hash en el entorno, comparación en tiempo constante.

Lo que se prueba aquí es la política de seguridad de CR-006: que la clave
nunca se guarda en claro, que un hash mal formado cierra el acceso en lugar
de abrirlo, y que la comparación pasa por `hmac.compare_digest`.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout

import pytest

from src.security import clave_reinicio

# 1000 iteraciones: en producción van 310 000, pero estos tests no tienen por
# qué tardar medio segundo cada uno. `verificar` lee las iteraciones del
# propio hash, así que el resultado es el mismo.
ITERACIONES_PRUEBA = 1_000
HASH_VALIDO = clave_reinicio.generar_hash("clave-secreta", iteraciones=ITERACIONES_PRUEBA)


class TestGeneracion:
    def test_el_formato_es_el_declarado(self) -> None:
        partes = HASH_VALIDO.split("$")
        assert len(partes) == 4
        assert partes[0] == "pbkdf2-sha256"
        assert partes[1] == str(ITERACIONES_PRUEBA)
        assert len(partes[2]) == 16 * 2  # 16 bytes de sal en hex
        assert len(partes[3]) == 32 * 2  # SHA-256 (32 bytes) en hex

    def test_dos_generaciones_no_comparten_sal(self) -> None:
        otra = clave_reinicio.generar_hash("clave-secreta", iteraciones=ITERACIONES_PRUEBA)
        assert otra != HASH_VALIDO

    def test_no_acepta_clave_vacia(self) -> None:
        with pytest.raises(ValueError, match="vacía"):
            clave_reinicio.generar_hash("")


class TestVerificacion:
    def test_la_clave_correcta_pasa(self) -> None:
        assert clave_reinicio.verificar("clave-secreta", HASH_VALIDO) is True

    @pytest.mark.parametrize("mala", ["", "clave-secretA", "clave-secreta ", "otra"])
    def test_cualquier_otra_no_pasa(self, mala: str) -> None:
        assert clave_reinicio.verificar(mala, HASH_VALIDO) is False

    @pytest.mark.parametrize(
        "guardado",
        [
            None,                 # sin configurar
            "",                   # vacío en el entorno
            "texto-sin-formato",  # no es un hash
            "bcrypt$2b$12$abc",   # algoritmo que no manejamos
            "pbkdf2-sha256$abc$sal$hash",  # iteraciones ilegibles
            "pbkdf2-sha256$1000$zz$zz",    # hex ilegible
            "pbkdf2-sha256$0$00$00",       # iteraciones no positivas
        ],
    )
    def test_un_hash_mal_formado_cierra_el_acceso_sin_tirar_nada(self, guardado: str | None) -> None:
        # Un error de configuración del servidor no puede ni tumbar el
        # endpoint ni —mucho menos— dejar el reinicio abierto.
        assert clave_reinicio.verificar("clave-secreta", guardado) is False

    def test_sin_clave_enviada_nada_pasa(self) -> None:
        assert clave_reinicio.verificar("", HASH_VALIDO) is False
        assert clave_reinicio.verificar(None, HASH_VALIDO) is False  # type: ignore[arg-type]

    def test_la_comparacion_pasa_por_compare_digest(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # No se puede medir el tiempo en un test, pero sí comprobar el cable:
        # si alguien lo cambia por `==`, esto falla.
        import hmac

        llamadas: list[tuple[bytes, bytes]] = []
        original = hmac.compare_digest

        def _espiar(a: bytes, b: bytes) -> bool:
            llamadas.append((a, b))
            return original(a, b)

        monkeypatch.setattr(hmac, "compare_digest", _espiar)
        assert clave_reinicio.verificar("clave-secreta", HASH_VALIDO) is True
        assert len(llamadas) == 1


class TestEntorno:
    def test_desde_entorno_lee_la_variable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(clave_reinicio.VARIABLE, HASH_VALIDO)
        assert clave_reinicio.desde_entorno() == HASH_VALIDO

    def test_sin_variable_devuelve_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # `None` = reinicio deshabilitado, no «cualquier clave vale».
        monkeypatch.delenv(clave_reinicio.VARIABLE, raising=False)
        assert clave_reinicio.desde_entorno() is None

    def test_un_valor_en_blanco_tambien_es_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(clave_reinicio.VARIABLE, "   ")
        assert clave_reinicio.desde_entorno() is None


class TestCli:
    def _correr(self, claves: list[str], monkeypatch: pytest.MonkeyPatch) -> tuple[int, str]:
        import getpass

        restantes = list(claves)
        monkeypatch.setattr(
            getpass, "getpass", lambda _p="": restantes.pop(0) if restantes else ""
        )
        salida = io.StringIO()
        with redirect_stdout(salida):
            codigo = clave_reinicio.main(["--iteraciones", str(ITERACIONES_PRUEBA)])
        return codigo, salida.getvalue()

    def test_genera_la_linea_para_el_entorno(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        codigo, salida = self._correr(["mi-clave", "mi-clave"], monkeypatch)
        assert codigo == 0
        assert f"{clave_reinicio.VARIABLE}=pbkdf2-sha256$" in salida
        # Nunca aparece la clave en claro.
        assert "mi-clave" not in salida

    def test_se_ren_si_las_dos_lecturas_no_coinciden(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        codigo, salida = self._correr(["una", "otra"], monkeypatch)
        assert codigo == 1
        assert clave_reinicio.VARIABLE not in salida

    def test_se_ren_si_la_clave_esta_vacia(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import getpass

        monkeypatch.setattr(getpass, "getpass", lambda _p="": "")
        assert clave_reinicio.main([]) == 1
