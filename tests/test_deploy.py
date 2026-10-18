"""Pruebas de los ayudantes de `deploy/m1/`.

Se prueban aquí, y no a mano, porque son los que sostienen el *rollback* del
reentrenamiento diario. `promote.sh` decide si restaura `active.json` según lo
que devuelva `check_artifact.py`, y si reinicia la inferencia o la deja caída
según `check_health.py`: un código de salida equivocado en cualquiera de los dos
convertiría una red de seguridad en un disparo contra el propio sistema.

Además se comprueban tres cosas que solo se rompen al editar texto y que ningún
otro test mira:

* la **sintaxis** de los scripts de shell (que ya costó un fallo: un heredoc
  con paréntesis dentro de un `$( )` de bash se interpretaba como cierre de la
  sustitución y el script no llegaba a ejecutarse),
* la **zona horaria** del timer, que es una decisión del SRS y no un detalle,
* el **mínimo privilegio** de la plantilla de entorno de M1.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import numpy as np
import torch

from src.ai.features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION
from src.ai.model import (
    DEFAULT_MODEL_CONFIG,
    NasdaqMLP,
    build_artifact,
    save_artifact,
)

RAIZ = Path(__file__).resolve().parents[1]
DEPLOY = RAIZ / "deploy" / "m1"
N_FEATURES = len(FEATURE_NAMES)
SEED = 20261017
PESOS_REALES = np.array([1.311, 0.397, 1.292], dtype=np.float64)


def _cargar(nombre: str) -> Any:
    """Importa un módulo suelto de `deploy/` por su ruta.

    No es un paquete de Python (vive en `deploy/`, fuera de `src/`, a propósito:
    `AGENTS.md` §15.4), así que no hay forma de importarlo por nombre.
    """
    ruta = DEPLOY / f"{nombre}.py"
    spec = importlib.util.spec_from_file_location(f"ritech_{nombre}", ruta)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo
    spec.loader.exec_module(modulo)
    return modulo


def _artefacto_valido(directorio: Path) -> Path:
    """Escribe un `.pt` de mentira y el `active.json` que lo apunta.

    El puntero lleva solo el nombre del archivo, que es la convención de
    `src.ai.model.ruta_desde_puntero` y por lo que la resolución no depende del
    directorio de trabajo.
    """
    torch.manual_seed(SEED)
    artefacto = build_artifact(
        NasdaqMLP(), model_type="mlp", model_config=dict(DEFAULT_MODEL_CONFIG),
        class_weights=PESOS_REALES,
        stats_mean=np.zeros(N_FEATURES, dtype=np.float32),
        stats_std=np.ones(N_FEATURES, dtype=np.float32),
        stable_band_pct=0.10, trained_at_ms=0, training_device="cpu",
    )
    ruta = save_artifact(artefacto, directorio / "modelo.pt")
    (directorio / "active.json").write_text(
        json.dumps({"path": "modelo.pt", "arquitectura": "mlp"}),
        encoding="utf-8",
    )
    return ruta


# ---------------------------------------------------------------------------
# Los scripts de shell tienen que al menos poderse leer por bash
# ---------------------------------------------------------------------------

def test_los_scripts_de_despliegue_tienen_sintaxis_valida() -> None:
    """`bash -n` sobre cada script.

    Poco glamoroso y ya imprescindible una vez: un bloque de Python dentro de
    `$( ... )` con paréntesis en el cuerpo hace que bash no logre cerrar la
    sustitución, el error se manifiesta decenas de líneas más abajo en un sitio
    que no lo merece, y el script ni se llega a ejecutar.
    """
    scripts = sorted(DEPLOY.glob("*.sh"))
    assert scripts, "no hay scripts en deploy/m1: ¿se ha movido el directorio?"

    for script in scripts:
        proceso = subprocess.run(
            ["bash", "-n", str(script)],
            capture_output=True, text=True, check=False,
        )
        assert proceso.returncode == 0, (
            f"{script.name} no es sintácticamente válido:\n{proceso.stderr}"
        )


def test_los_scripts_son_ejecutables() -> None:
    """Se invocan por su ruta directamente en `docs/despliegue.md`."""
    for script in sorted(DEPLOY.glob("*.sh")):
        assert script.stat().st_mode & 0o111, f"{script.name} no tiene permiso de ejecución"


def test_verify_se_ejecuta_en_una_ruta_con_espacios() -> None:
    """Regresión de un fallo real del PASO 3.

    `verify.sh` hacía `ok "intérprete: $($PYTHON -V 2>&1)"` sin comillas
    alrededor de `$PYTHON`. Dentro de una sustitución `$( )` bash trocea por
    espacios, así que con

        RAIZ=/home/giyu/Escritorio/Ingenieria de Calidad

    intentaba ejecutar `/home/giyu/Escritorio/Ingenieria`, que no existe. El
    error salía y **encima se reportaba como ✓**, porque lo que `ok` mira es el
    código de salida de la sustitución, que era el de `true`.

    No hace falta maquillar nada para reproducirlo: este repositorio ya está en
    una ruta con espacios. Por eso el test no fija una ruta falsa, sino que
    corre el script tal cual.

    Salida esperada en este equipo de desarrollo: **1**, porque aquí no hay
    systemd ni GPU. El código que no debe aparecer es el del propio bash.
    """
    proceso = subprocess.run(
        [str(DEPLOY / "verify.sh")],
        capture_output=True, text=True, check=False, cwd=RAIZ,
        env={**os.environ, "RITECH_ENV_FILE": str(RAIZ / ".env.m1.example")},
        timeout=180,
    )
    salida = proceso.stdout + proceso.stderr

    assert proceso.returncode in (0, 1), salida
    assert not re.search(r"verify\.sh: line \d+:", salida), (
        "bash se quejó al ejecutar verify.sh (ruta con espacios mal citada):\n"
        + "\n".join(l for l in salida.splitlines() if "verify.sh: line" in l)
    )
    assert "No such file or directory" not in salida, salida


# ---------------------------------------------------------------------------
# check_artifact.py — decide si el reentrenamiento se promueve o se revierte
# ---------------------------------------------------------------------------

class TestCheckArtifact:
    def test_sin_argumentos_no_adivina(self, capsys) -> None:
        modulo = _cargar("check_artifact")
        assert modulo.main([]) == 2
        assert "uso:" in capsys.readouterr().err

    def test_un_puntero_inexistente_falla_con_mensaje_accionable(self, tmp_path, capsys) -> None:
        """La señal más frecuente: la máquina recién instalada, sin modelo."""
        modulo = _cargar("check_artifact")
        codigo = modulo.main([str(tmp_path / "active.json")])
        assert codigo == 1
        assert "no existe el puntero" in capsys.readouterr().err

    def test_un_puntero_con_una_ruta_de_la_convencion_vieja_indica_reentrenar(
            self, tmp_path, capsys) -> None:
        """Regresión de `docs/despliegue.md` §10.1.

        La ruta guardada contra la raíz del proyecto (`models/x.pt`) resuelta
        contra el directorio del puntero da `models/models/x.pt`. No basta con
        fallar: quien lo vea está en un arranque y necesita saber qué hacer.
        """
        modulo = _cargar("check_artifact")
        puntero = tmp_path / "active.json"
        puntero.write_text(json.dumps({"path": "models/modelo.pt"}), encoding="utf-8")

        assert modulo.main([str(puntero)]) == 1
        assert "reentrenar" in capsys.readouterr().err

    def test_un_puntero_valido_devuelve_ruta_tipo_y_esquema(self, tmp_path, capsys) -> None:
        """La salida la trocea `promote.sh` con `cut -d'|'`: el orden es contrato."""
        modulo = _cargar("check_artifact")
        ruta = _artefacto_valido(tmp_path)

        assert modulo.main([str(tmp_path / "active.json")]) == 0
        campos = capsys.readouterr().out.strip().split("|")
        assert campos == [str(ruta), "mlp", FEATURE_SCHEMA_VERSION]

    def test_un_archivo_basura_que_no_es_un_artefacto_no_pasa_en_blanco(
            self, tmp_path, capsys) -> None:
        """El puntero apunta a algo y ese algo no se puede cargar: error, no traza."""
        modulo = _cargar("check_artifact")
        (tmp_path / "modelo.pt").write_bytes(b"esto no es un torch.save")
        (tmp_path / "active.json").write_text(
            json.dumps({"path": "modelo.pt"}), encoding="utf-8"
        )

        assert modulo.main([str(tmp_path / "active.json")]) == 1
        assert "no se pudo cargar" in capsys.readouterr().err

    def test_se_puede_ejecutar_desde_un_directorio_de_trabajo_al_ajeno(
            self, tmp_path, monkeypatch, capsys) -> None:
        """Los scripts lo invocan tras un `cd`, pero no debería importarle."""
        modulo = _cargar("check_artifact")
        ruta = _artefacto_valido(tmp_path)

        otro = tmp_path / "trabajo"
        otro.mkdir()
        monkeypatch.chdir(otro)

        assert modulo.main([str(tmp_path / "active.json")]) == 0
        assert capsys.readouterr().out.strip().split("|")[0] == str(ruta)


# ---------------------------------------------------------------------------
# check_health.py — decide si el reentrenamiento da por buena la promoción
# ---------------------------------------------------------------------------

class TestCheckHealth:
    def test_la_url_sale_del_entorno_y_no_de_un_argumento(self, monkeypatch) -> None:
        """Si la URL se escribiera a mano en el script, alguien la cambiaría en
        un sitio y no en el otro, y se comprobaría un servicio que no existe."""
        modulo = _cargar("check_health")
        monkeypatch.setenv("INFERENCE_HOST", "10.0.0.7")
        monkeypatch.setenv("INFERENCE_PORT", "9000")
        assert modulo.url_del_servicio() == "http://10.0.0.7:9000/health"

    def test_sin_servicio_falla_limpio_con_la_url_en_el_mensaje(self, monkeypatch, capsys) -> None:
        """`--tope-s 0` es una sola consulta: no hace falta esperar en el test."""
        modulo = _cargar("check_health")
        monkeypatch.setenv("INFERENCE_HOST", "127.0.0.1")
        monkeypatch.setenv("INFERENCE_PORT", "1")   # nadie escucha en el puerto 1

        assert modulo.main(["check_health.py", "--tope-s", "0"]) == 1
        error = capsys.readouterr().err
        assert "/health no respondió" in error
        assert "http://127.0.0.1:1/health" in error

    def test_un_tope_negativo_es_un_error_de_uso_no_de_red(self) -> None:
        modulo = _cargar("check_health")
        assert modulo.main(["check_health.py", "--tope-s", "-3"]) == 2

    def test_una_respuesta_sin_los_campos_que_espera_m2_no_pasa(
            self, monkeypatch, capsys) -> None:
        """M2 confía en `feature_schema_version` para decidir si le hace caso.

        Si `/health` respondiera 200 pero sin esa columna, no se sabría si se
        sirve el modelo correcto — y no poder comprobarlo es peor que no
        responder, porque obliga a confiar. Por eso no basta con mirar el
        código de estado.
        """
        modulo = _cargar("check_health")
        monkeypatch.setattr(
            modulo, "consultar",
            lambda url, timeout_s: ({"estado": "ok", "model_type": "mlp"}, ""),
        )

        assert modulo.main(["check_health.py", "--tope-s", "0"]) == 1
        assert "feature_schema_version" in capsys.readouterr().err

    def test_consultar_sin_servicio_devuelve_cuerpo_nulo_y_un_motivo(self) -> None:
        modulo = _cargar("check_health")
        cuerpo, motivo = modulo.consultar("http://127.0.0.1:1/health", timeout_s=0.1)
        assert cuerpo is None
        assert motivo


# ---------------------------------------------------------------------------
# Las unidades systemd y la plantilla de M1 — decisiones que solo se rompen al editar
# ---------------------------------------------------------------------------

def test_el_timer_dispare_a_la_hora_que_fija_el_srs() -> None:
    """20:30 de Colombia, no la zona del servidor.

    El Este de EE. UU. tiene horario de verano y Colombia no: con la zona del
    servidor, la misma línea habría caído a las 19:30 de Colombia en verano,
    antes de la hora mínima que exige el SRS §4.3. Es una decisión de
    requisito, no una preferencia, por eso está aquí.
    """
    texto = (DEPLOY / "ritech-retrain.timer").read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* 20:30:00 America/Bogota" in texto
    assert "Persistent=true" in texto, "sin Persistent se salta la corrida en silencio"


def test_la_plantilla_de_m1_no_contiene_secretos_de_otras_maquinas() -> None:
    """Mínimo privilegio (`AGENTS.md` §15.2), comprobado sobre el texto.

    `deploy/m1/verify.sh` lo comprueba en el equipo ya instalado; aquí se
    impide que la plantilla misma crezca hacia el lado equivocado. Es la clase
    de fallo que no da ningún síntoma: M1 funcionaría perfectamente con
    credenciales que no debería tener.
    """
    plantilla = (RAIZ / ".env.m1.example").read_text(encoding="utf-8")

    prohibidas = (
        "TWELVE_DATA_API_KEY", "POSTGRES_PASSWORD", "POSTGRES_USER",
        "IB_API_KEY", "IB_ACCOUNT_ID", "CREDENTIALS_MASTER_KEY",
    )
    for variable in prohibidas:
        assert not any(
            linea.strip().startswith(f"{variable}=") and len(linea.split("=", 1)[1].strip()) > 0
            for linea in plantilla.splitlines()
        ), f".env.m1.example declara {variable}, que es de la Máquina 2"


def test_los_scripts_de_m1_no_tienen_rutas_absolutas_de_esta_maquina() -> None:
    """Los `deploy/` son la única parte con rutas fijas (§15.4), y son las de
    la Máquina 1 en producción: `/opt/ritech` y `/etc/ritech`.

    Se comprueba que no aparezcan rutas de esta máquina de desarrollo, que es
    cómo se cuela un `home` de quien escribió el código dentro de un artefacto
    que va a otra computadora.
    """
    for archivo in sorted(DEPLOY.iterdir()):
        if not archivo.is_file():
            continue
        texto = archivo.read_text(encoding="utf-8", errors="ignore")
        assert "/home/giyu" not in texto, f"{archivo.name} lleva una ruta de esta máquina"
