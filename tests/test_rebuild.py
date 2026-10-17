"""Pruebas de la migración CR-001 (`src/ingestion/rebuild.py`).

Lo crítico aquí es la **seguridad**: la operación destructiva solo puede ocurrir
con confirmación explícita, y nunca debe quedar al alcance de un *default*.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from src.ingestion import rebuild as rebuild_mod
from src.ingestion.backfill import BackfillReport
from src.ingestion.rebuild import rebuild

_CINCO = ["price_close", "price_high", "price_low", "price_open", "volume"]
_TRES = ["price_close", "price_open", "volume"]


def _provider() -> MagicMock:
    provider = MagicMock()
    provider.api_key = "k"
    return provider


class _BackfillStub:
    def __init__(self, errores: list[str] | None = None) -> None:
        self.errores = errores or []

    async def __call__(self, *args: Any, **kwargs: Any) -> BackfillReport:
        return BackfillReport(
            peticiones=8, velas_unicas=39_000, velas_escritas=39_000,
            errores=list(self.errores),
        )


def _ejecutar(*, confirm: bool, dry_run: bool = False,
              conteos: list[int] | None = None,
              campos: list[str] | None = None,
              errores: list[str] | None = None) -> Any:
    """Ejecuta `rebuild` con las funciones de consulta parcheadas."""
    secuencia = list(conteos if conteos is not None else [10_000, 0, 39_000])
    campos_valor = list(campos if campos is not None else _CINCO)

    with patch.object(rebuild_mod, "_count_puntos",
                      side_effect=lambda _i: secuencia.pop(0) if secuencia else 0), \
         patch.object(rebuild_mod, "_campos_presentes",
                      return_value=campos_valor), \
         patch.object(rebuild_mod, "_borrar_todo") as borrar, \
         patch.object(rebuild_mod, "run_backfill", new=_BackfillStub(errores)):
        report = asyncio.run(rebuild(
            MagicMock(), _provider(), trading_days=500,
            confirm=confirm, dry_run=dry_run,
        ))
        return report, borrar


class TestConfirmacionObligatoria:
    def test_sin_confirm_wipe_no_borra_nada(self) -> None:
        with pytest.raises(SystemExit) as exc:
            _ejecutar(confirm=False)
        assert "--confirm-wipe" in str(exc.value)

    def test_sin_confirm_wipe_no_llama_al_borrado(self) -> None:
        # La negativa debe salir ANTES de tocar el bucket.
        with patch.object(rebuild_mod, "_borrar_todo") as borrar, \
             patch.object(rebuild_mod, "run_backfill", new=_BackfillStub()):
            with pytest.raises(SystemExit):
                asyncio.run(rebuild(MagicMock(), _provider(), trading_days=500,
                                    confirm=False, dry_run=False))
            borrar.assert_not_called()

    def test_sin_confirm_wipe_no_consulta_nada(self) -> None:
        with patch.object(rebuild_mod, "_count_puntos") as contar, \
             patch.object(rebuild_mod, "run_backfill", new=_BackfillStub()):
            with pytest.raises(SystemExit):
                asyncio.run(rebuild(MagicMock(), _provider(), trading_days=500,
                                    confirm=False, dry_run=False))
            contar.assert_not_called()


class TestDryRun:
    def test_dry_run_no_borra_ni_reingresa(self) -> None:
        report, borrar = _ejecutar(confirm=True, dry_run=True)
        borrar.assert_not_called()
        assert report.borrados is False
        assert report.velas_escritas == 0

    def test_dry_run_no_requiere_confirmacion(self) -> None:
        # Informar sin tocar nada no debe exigir el flag de confirmación.
        report, borrar = _ejecutar(confirm=False, dry_run=True)
        borrar.assert_not_called()
        assert report.antes_puntos == 10_000

    def test_dry_run_informa_el_estado_previo(self) -> None:
        report, _ = _ejecutar(confirm=True, dry_run=True, campos=_TRES)
        assert report.antes_puntos == 10_000
        assert report.faltantes == ["price_high", "price_low"]


class TestMigracion:
    def test_migracion_exitosa_borra_y_reingresa(self) -> None:
        report, borrar = _ejecutar(confirm=True)
        borrar.assert_called_once()
        assert report.borrados is True
        assert report.velas_escritas == 39_000
        assert report.faltantes == []
        assert set(report.campos_detectados) == set(_CINCO)

    def test_se_detiene_si_quedan_puntos_tras_el_borrado(self) -> None:
        with pytest.raises(SystemExit) as exc, \
             patch.object(rebuild_mod, "_count_puntos", side_effect=[10_000, 7, 7]), \
             patch.object(rebuild_mod, "_campos_presentes", return_value=_CINCO), \
             patch.object(rebuild_mod, "_borrar_todo"), \
             patch.object(rebuild_mod, "run_backfill", new=_BackfillStub()):
            asyncio.run(rebuild(MagicMock(), _provider(), trading_days=500,
                                confirm=True, dry_run=False))
        assert "quedaron" in str(exc.value)

    def test_se_detiene_si_siguen_faltando_campos(self) -> None:
        # Reingresó, pero los campos siguen ausentes → migración incompleta.
        with pytest.raises(SystemExit) as exc, \
             patch.object(rebuild_mod, "_count_puntos", side_effect=[10_000, 0, 39_000]), \
             patch.object(rebuild_mod, "_campos_presentes", return_value=_TRES), \
             patch.object(rebuild_mod, "_borrar_todo"), \
             patch.object(rebuild_mod, "run_backfill", new=_BackfillStub()):
            asyncio.run(rebuild(MagicMock(), _provider(), trading_days=500,
                                confirm=True, dry_run=False))
        assert "faltando campos" in str(exc.value)

    def test_se_detiene_si_el_backfill_reporta_errores(self) -> None:
        with pytest.raises(SystemExit), \
             patch.object(rebuild_mod, "_count_puntos", side_effect=[10_000, 0]), \
             patch.object(rebuild_mod, "_campos_presentes", return_value=_CINCO), \
             patch.object(rebuild_mod, "_borrar_todo"), \
             patch.object(rebuild_mod, "run_backfill",
                          new=_BackfillStub(errores=["cuota agotada"])):
            asyncio.run(rebuild(MagicMock(), _provider(), trading_days=500,
                                confirm=True, dry_run=False))


class TestAlcance:
    def test_el_predicado_de_borrado_usa_sintaxis_de_la_api_delete(self) -> None:
        """Regresión: `==` devuelve 400 en la API delete de InfluxDB 2.x.

        Verificado en vivo contra v2.7.12: `_measurement=="X"` → 400
        "bad tag value"; `_measurement=X` → 204. Un error aquí dejaría el
        bucket a medias.
        """
        assert rebuild_mod._DELETE_PREDICATE == "_measurement=NasdaqTick"
        assert "==" not in rebuild_mod._DELETE_PREDICATE
        assert " " not in rebuild_mod._DELETE_PREDICATE

    def test_los_campos_esperados_coinciden_con_el_sdd(self) -> None:
        # SDD §3.1.2 / SRS §4.2.2 (CR-001): los cinco campos de la vela.
        assert set(rebuild_mod._CAMPOS_ESPERADOS) == {
            "price_open", "price_high", "price_low", "price_close", "volume",
        }

    def test_es_un_cli_y_no_lo_importa_el_pipeline(self) -> None:
        """La migración no debe quedar al alcance de los módulos en operación."""
        from src.ingestion import backfill, engine, scheduler
        for modulo in (backfill, engine, scheduler):
            fuente = open(modulo.__file__, encoding="utf-8").read()
            assert "rebuild" not in fuente, (
                f"{modulo.__name__} no debe referenciar la migración"
            )

    def test_el_modulo_declara_que_no_es_del_pipeline(self) -> None:
        assert "No es parte del pipeline" in (rebuild_mod.__doc__ or "")


def test_la_migracion_corre_sin_torch() -> None:
    """Como el backfill: debe operar en M2 con el grupo `base`, sin torch."""
    import subprocess
    import sys
    from pathlib import Path

    raiz = Path(rebuild_mod.__file__).resolve().parents[2]
    bloqueo = (
        "import sys\n"
        "class Bloqueo:\n"
        "    def find_module(self, name, path=None):\n"
        "        return self if name == 'torch' or name.startswith('torch.') else None\n"
        "    def load_module(self, name):\n"
        "        raise ImportError('torch no debe importarse en la migracion')\n"
        "sys.meta_path.insert(0, Bloqueo())\n"
        "import src.ingestion.rebuild\n"
        "print('OK sin torch')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", bloqueo], capture_output=True, text=True,
        cwd=str(raiz), env={"PYTHONPATH": str(raiz), "PATH": "/usr/bin:/bin"},
        timeout=120,
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr}"
    assert "OK sin torch" in proc.stdout
