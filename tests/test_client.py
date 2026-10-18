"""Pruebas del cliente de inferencia (`src/ai/client.py`).

Lo que se protege aquí es el **lado M2 de la frontera**, que tiene tres formas
de fallar en silencio y una en ruido:

1. Enviar una ventana mal formada (falla en silencio si no se valida aquí).
2. Aceptarse con un esquema de features distinto al con que se entrenó
   (falla en silencio: la forma del tensor es la misma, 120, aunque el
   significado de las columnas cambió).
3. Interpretar una respuesta que no cumple el contrato.

Y la cuarta, que debe fallar ruidosamente: que `client.py` llegue a importar
`torch`. M2 no debería instalar el grupo `ai` para pedirle una predicción a M1.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import httpx
import numpy as np
import pytest

from src.ai.client import (
    ErrorDeEsquema,
    ErrorDeInferencia,
    InferenceClient,
    cliente_desde_entorno,
)
from src.ai.features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION, LABEL_NAMES, WINDOW

URL = "http://127.0.0.1:8000"
N_FEATURES = len(FEATURE_NAMES)


def ventana() -> np.ndarray:
    return np.random.RandomState(20261017).randn(WINDOW, N_FEATURES).astype(np.float32)


def respuesta_valida(**cambios: Any) -> dict[str, Any]:
    """Cuerpo `/predict` conforme al contrato, con los cambios que pida el test."""
    cuerpo = {
        "trend_prediction": "STABLE",
        "confidence_score": 0.83,
        "technical_rationale": "XAI de prueba",
        "posterior_corregido": {nombre: 1 / 3 for nombre in LABEL_NAMES.values()},
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "artifact_format_version": 1,
        "model_type": "mlp",
        "stable_band_pct": 0.10,
        "generado_en_ms": 1791316500000,
    }
    cuerpo.update(cambios)
    return cuerpo


@pytest.fixture()
def montar_transporte(monkeypatch: pytest.MonkeyPatch) -> Callable[[Callable], None]:
    """Sustituye `httpx.Client` por uno con un `MockTransport`.

    Se sustituye el `Client` y no la instancia porque el cliente real crea y
    cierra una conexión por llamada: así se ejercita el código que se va a
    desplegar, incluida la gestión de errores de transporte.
    """

    def _montar(handler: Callable[[httpx.Request], httpx.Response]) -> None:
        # Se captura la clase real ANTES de parchear: si no, la fábrica se
        # llamaría a sí misma y se entraría en recursión infinita.
        ClienteReal = httpx.Client

        def fabrica(*args: Any, **kwargs: Any) -> httpx.Client:
            kwargs["transport"] = httpx.MockTransport(handler)
            return ClienteReal(*args, **kwargs)

        monkeypatch.setattr(httpx, "Client", fabrica)

    return _montar


# ---------------------------------------------------------------------------
# Configuración — la URL sale del entorno, nunca del código (§15.1)
# ---------------------------------------------------------------------------

class TestConfiguracion:
    def test_sin_url_no_se_puede_construir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("INFERENCE_URL", raising=False)
        with pytest.raises(ErrorDeInferencia, match="INFERENCE_URL"):
            InferenceClient()

    def test_la_url_puede_venir_del_entorno(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("INFERENCE_URL", URL)
        assert InferenceClient().url == URL

    def test_el_argumento_tiene_prioridad_sobre_el_entorno(self, monkeypatch) -> None:
        monkeypatch.setenv("INFERENCE_URL", "http://otra:9000")
        assert InferenceClient(URL).url == URL

    def test_se_ignoran_las_barras_finales(self) -> None:
        assert InferenceClient(f"{URL}/").url == URL

    def test_cliente_desde_entorno_lee_el_timeout(self, monkeypatch) -> None:
        monkeypatch.setenv("INFERENCE_URL", URL)
        monkeypatch.setenv("INFERENCE_TIMEOUT_S", "2.5")
        assert cliente_desde_entorno()._timeout_s == 2.5

    def test_un_timeout_no_numerico_no_se_admite(self, monkeypatch) -> None:
        monkeypatch.setenv("INFERENCE_URL", URL)
        monkeypatch.setenv("INFERENCE_TIMEOUT_S", "dos segundos")
        with pytest.raises(ErrorDeInferencia, match="INFERENCE_TIMEOUT_S"):
            cliente_desde_entorno()


# ---------------------------------------------------------------------------
# Validación local de la ventana
# ---------------------------------------------------------------------------

class TestValidacionDeVentana:
    def test_acepta_una_ventana_correcta(self) -> None:
        assert InferenceClient._validar_ventana(ventana()).shape == (WINDOW, N_FEATURES)

    def test_acepta_listas_anidadas(self) -> None:
        lista = ventana().tolist()
        assert InferenceClient._validar_ventana(lista).shape == (WINDOW, N_FEATURES)

    def test_rechaza_una_ventana_de_mal_tamano(self) -> None:
        with pytest.raises(ValueError, match="ventana"):
            InferenceClient._validar_ventana(np.zeros((WINDOW - 1, N_FEATURES)))

    def test_rechaza_una_ventana_plana(self) -> None:
        # La forma es 120 en los dos casos; sin la comprobación de dimensiones
        # se enviaría una ventana que el servidor rechazaría con peor mensaje.
        with pytest.raises(ValueError, match="ventana"):
            InferenceClient._validar_ventana(np.zeros(WINDOW * N_FEATURES))

    def test_rechaza_una_ventana_con_nan(self) -> None:
        mala = ventana()
        mala[0, 0] = np.nan
        with pytest.raises(ValueError, match="NaN"):
            InferenceClient._validar_ventana(mala)

    def test_rechaza_una_ventana_con_infinito(self) -> None:
        mala = ventana()
        mala[3, 5] = np.inf
        with pytest.raises(ValueError, match="NaN"):
            InferenceClient._validar_ventana(mala)

    def test_una_ventana_validada_llega_al_servidor_completa(self, montar_transporte) -> None:
        """Si el cliente recorta o reordena la ventana, el modelo predice otra cosa."""
        capturado: dict[str, Any] = {}

        def handler(peticion: httpx.Request) -> httpx.Response:
            capturado.update(json.loads(peticion.content.decode("utf-8")))
            return httpx.Response(200, json=respuesta_valida())

        montar_transporte(handler)
        original = ventana()
        InferenceClient(URL).predecir(original)
        assert np.allclose(np.asarray(capturado["features"]), original, atol=1e-6)
        assert len(capturado["features"]) == WINDOW


# ---------------------------------------------------------------------------
# Respuesta — contrato y esquema
# ---------------------------------------------------------------------------

class TestRespuesta:
    def test_construye_la_prediccion_completa(self, montar_transporte) -> None:
        montar_transporte(lambda p: httpx.Response(200, json=respuesta_valida()))
        p = InferenceClient(URL).predecir(ventana())
        assert p.trend_prediction == "STABLE"
        assert p.confidence_score == pytest.approx(0.83)
        assert p.technical_rationale == "XAI de prueba"
        assert p.model_type == "mlp"
        assert p.stable_band_pct == pytest.approx(0.10)
        assert set(p.posterior_corregido) == set(LABEL_NAMES.values())
        assert p.latencia_ms >= 0

    def test_el_esquema_remoto_se_compara_con_el_local(self, montar_transporte) -> None:
        montar_transporte(lambda p: httpx.Response(200, json=respuesta_valida()))
        p = InferenceClient(URL).predecir(ventana())
        assert p.feature_schema_version == FEATURE_SCHEMA_VERSION

    def test_rechaza_un_esquema_remoto_distinto(self, montar_transporte) -> None:
        """La trampa silenciosa del contrato: misma forma, otro significado.

        Si esto pasara, el sistema seguiría prediciendo sobre 120 números que
        ya no significan lo mismo, sin error y sin alerta.
        """
        montar_transporte(lambda p: httpx.Response(
            200, json=respuesta_valida(feature_schema_version="2020-01-01")
        ))
        with pytest.raises(ErrorDeEsquema, match="entrenado con el esquema"):
            InferenceClient(URL).predecir(ventana())

    def test_verificar_esquema_acepta_los_iguales(self, montar_transporte) -> None:
        cuerpo = {"estado": "ok", "feature_schema_version": FEATURE_SCHEMA_VERSION}
        montar_transporte(lambda p: httpx.Response(200, json=cuerpo))
        assert InferenceClient(URL).verificar_esquema() == FEATURE_SCHEMA_VERSION

    def test_verificar_esquema_se_niega_a_un_esquema_ajeno(self, montar_transporte) -> None:
        cuerpo = {"estado": "ok", "feature_schema_version": "2020-01-01"}
        montar_transporte(lambda p: httpx.Response(200, json=cuerpo))
        with pytest.raises(ErrorDeEsquema, match="incompatible"):
            InferenceClient(URL).verificar_esquema()

    def test_verificar_esquema_tambien_se_niega_si_no_declaran_version(
            self, montar_transporte) -> None:
        montar_transporte(lambda p: httpx.Response(200, json={"estado": "ok"}))
        with pytest.raises(ErrorDeEsquema, match="sin declarar"):
            InferenceClient(URL).verificar_esquema()


# ---------------------------------------------------------------------------
# Fallos de transporte y de respuesta
# ---------------------------------------------------------------------------

class TestFallos:
    def test_un_error_del_servidor_no_se_disfraza_de_prediccion(self, montar_transporte) -> None:
        def handler(peticion: httpx.Request) -> httpx.Response:
            return httpx.Response(422, json={"detail": "la ventana no es válida"})

        montar_transporte(handler)
        with pytest.raises(ErrorDeInferencia, match="422"):
            InferenceClient(URL).predecir(ventana())

    def test_una_respuesta_que_no_es_json_se_rechaza(self, montar_transporte) -> None:
        montar_transporte(lambda p: httpx.Response(200, text="<html>no soy json</html>"))
        with pytest.raises(ErrorDeInferencia, match="no es JSON"):
            InferenceClient(URL).salud()

    def test_una_respuesta_que_no_es_un_objeto_se_rechaza(self, montar_transporte) -> None:
        montar_transporte(lambda p: httpx.Response(200, json=[1, 2, 3]))
        with pytest.raises(ErrorDeInferencia, match="se esperaba un objeto"):
            InferenceClient(URL).salud()

    def test_una_respuesta_incompleta_se_rechaza(self, montar_transporte) -> None:
        montar_transporte(lambda p: httpx.Response(200, json={"trend_prediction": "UP"}))
        with pytest.raises(ErrorDeInferencia, match="no cumple el contrato"):
            InferenceClient(URL).predecir(ventana())

    def test_un_servidor_caido_se_reporta_claramente(self, montar_transporte) -> None:
        def handler(peticion: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        montar_transporte(handler)
        with pytest.raises(ErrorDeInferencia, match="sin respuesta"):
            InferenceClient(URL).predecir(ventana())

    def test_una_confianza_fuera_de_rango_se_rechaza(self, montar_transporte) -> None:
        """Antes de que el RiskManager decida con un número imposible."""
        montar_transporte(
            lambda p: httpx.Response(200, json=respuesta_valida(confidence_score=1.7))
        )
        with pytest.raises(ErrorDeInferencia, match="confidence_score fuera de"):
            InferenceClient(URL).predecir(ventana())

    def test_una_tendencia_fuera_de_las_tres_se_rechaza(self, montar_transporte) -> None:
        montar_transporte(
            lambda p: httpx.Response(200, json=respuesta_valida(trend_prediction="LATERAL"))
        )
        with pytest.raises(ErrorDeInferencia, match="trend_prediction fuera de"):
            InferenceClient(URL).predecir(ventana())

    def test_un_posterior_con_clases_de_más_se_rechaza(self, montar_transporte) -> None:
        """Un posterior que no reparte las tres clases no sirve para decidir."""
        posterior = {nombre: 1 / 3 for nombre in LABEL_NAMES.values()}
        posterior["LATERAL"] = 0.0
        montar_transporte(
            lambda p: httpx.Response(200, json=respuesta_valida(posterior_corregido=posterior))
        )
        with pytest.raises(ErrorDeInferencia, match="posterior_corregido"):
            InferenceClient(URL).predecir(ventana())


# ---------------------------------------------------------------------------
# El cliente no arrastra torch
# ---------------------------------------------------------------------------

class TestSinTorch:
    def test_el_cliente_no_importa_torch(self) -> None:
        """M2 pide predicciones sin instalar el grupo `ai`.

        Si `client.py` llegara a importar torch, la Máquina 2 necesitaría dos
        gigas de framework de deep learning para llamar por HTTP a M1, y el
        Kill-switch tendría que cargar esa librería antes de poder cortar.
        """
        raiz = Path(__file__).resolve().parents[1]
        bloqueo = (
            "import sys\n"
            "class Bloqueo:\n"
            "    def find_spec(self, fullname, path=None, target=None):\n"
            "        if fullname == 'torch' or fullname.startswith('torch.'):\n"
            "            raise ImportError('torch no debe importarse en src.ai.client')\n"
            "        return None\n"
            "sys.meta_path.insert(0, Bloqueo())\n"
            "import src.ai.client\n"
            "print('OK sin torch')\n"
        )
        entorno = os.environ.copy()
        entorno["PYTHONPATH"] = str(raiz)
        proc = subprocess.run(
            [sys.executable, "-c", bloqueo], capture_output=True, text=True,
            cwd=str(raiz), env=entorno, timeout=120,
        )
        assert proc.returncode == 0, proc.stderr
        assert "OK sin torch" in proc.stdout
