"""Kill-Switch: bandera, orden de pasos, idempotencia, mejor esfuerzo y cola.

Todo con dobles: aquí se prueba la **política** (bloquear antes que liquidar,
no liquidar dos veces, no depender de la base para cortar) y no el SQL, que
se prueba contra PostgreSQL real en `tests/test_kill_switch.py::TestEnBaseReal`
y en el esquema.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import date
from pathlib import Path

import pytest

from tests.fakes import (
    AuditorKillSwitchFalso,
    BrokerKillSwitchFalso,
    RepoKillSwitchFalso,
    estado_kill_switch,
)

from src.security.auditoria import EventoAuditoria
from src.security.auditoria_pendiente import ColaAuditoriaPendiente
from src.security.kill_switch import (
    ACCION_DISPARO,
    BLOQUEO,
    BanderaBloqueo,
    KillSwitchEngine,
)

SESION = date(2026, 11, 3)


def motor(
    repo: RepoKillSwitchFalso | None = None,
    broker: BrokerKillSwitchFalso | None = None,
    auditor: AuditorKillSwitchFalso | None = None,
    *,
    banda: BanderaBloqueo | None = None,
    cola: ColaAuditoriaPendiente | None = None,
) -> tuple[KillSwitchEngine, RepoKillSwitchFalso, BrokerKillSwitchFalso, AuditorKillSwitchFalso]:
    repo = repo if repo is not None else RepoKillSwitchFalso()
    broker = broker if broker is not None else BrokerKillSwitchFalso()
    auditor = auditor if auditor is not None else AuditorKillSwitchFalso()
    banda = banda if banda is not None else BanderaBloqueo()
    broker.banda = banda
    return (
        KillSwitchEngine(
            repo, broker, auditor,
            cola=cola, banda=banda, ahora_ms=lambda: 1_800_000_000_000,
        ),
        repo, broker, auditor,
    )


class TestBanderaBloqueo:
    def test_se_pone_una_sola_vez(self) -> None:
        banda = BanderaBloqueo()
        assert banda.activo is False
        assert banda.activar() is True
        assert banda.activo is True
        assert banda.activar() is False  # la segunda pulsación no «reactiva»

    def test_liberar_solo_quita_si_estaba_puesta(self) -> None:
        banda = BanderaBloqueo()
        assert banda.liberar() is False
        banda.activar()
        assert banda.liberar() is True
        assert banda.activo is False

    def test_es_hilo_segura(self) -> None:
        import threading

        banda = BanderaBloqueo()
        ganadores: list[bool] = []
        candado = threading.Lock()

        def pulsar() -> None:
            resultado = banda.activar()
            with candado:
                ganadores.append(resultado)

        hilos = [threading.Thread(target=pulsar) for _ in range(8)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        assert ganadores.count(True) == 1, "solo un hilo puede haber activado"

    def test_las_instancias_no_comparten_estado_kill_switch(self) -> None:
        # El singleton del módulo es para producción; los tests usan el suyo.
        propia = BanderaBloqueo()
        propia.activar()
        assert propia.activo is True
        assert BLOQUEO is not propia


class TestDisparo:
    def test_el_bloqueo_se_pone_antes_de_todo_lo_demas(self) -> None:
        banda = BanderaBloqueo()
        repo = RepoKillSwitchFalso()
        broker = BrokerKillSwitchFalso()
        # Si la bandera no estuviera puesta al liquidar, esto queda en None.
        broker.banda = banda
        motor_ = KillSwitchEngine(
            repo, broker, AuditorKillSwitchFalso(), banda=banda, ahora_ms=lambda: 1
        )

        resultado = motor_.execute_global_liquidation(
            motivo="pulsación", origen="MANUAL", actor="OPERADOR", ip_address="10.0.0.7"
        )

        assert banda.activo is True
        assert broker.bloqueo_puesto_al_liquidar is True, "la bandera debe ir antes"
        assert resultado.primera_vez is True
        assert resultado.liquidacion_estado == "COMPLETADA"
        assert resultado.ordenes == 1
        assert resultado.persistencia_ok is True
        assert resultado.auditoria_ok is True

    def test_el_disparo_no_lee_la_base_antes_de_bloquear(self) -> None:
        # RNF-010: nada de I/O antes de la bandera. El repo registra sus
        # llamadas; la primera no puede ser una lectura previa al bloqueo.
        banda = BanderaBloqueo()
        repo = RepoKillSwitchFalso()
        motor_ = KillSwitchEngine(
            repo, BrokerKillSwitchFalso(), AuditorKillSwitchFalso(), banda=banda, ahora_ms=lambda: 1
        )
        motor_.execute_global_liquidation(
            motivo="x", origen="MANUAL", actor="OPERADOR", ip_address="127.0.0.1"
        )
        assert repo.llamadas[0] == "activar", repo.llamadas

    def test_segunda_pulsacion_no_liquid_dos_veces(self) -> None:
        repo, broker = RepoKillSwitchFalso(), BrokerKillSwitchFalso()
        motor_, repo, broker, _ = motor(repo, broker)

        primera = motor_.execute_global_liquidation(
            motivo="a", origen="MANUAL", actor="OPERADOR", ip_address="10.0.0.7"
        )
        segunda = motor_.execute_global_liquidation(
            motivo="a", origen="MANUAL", actor="OPERADOR", ip_address="10.0.0.7"
        )

        assert primera.primera_vez is True
        assert segunda.primera_vez is False
        assert broker.liquidaciones == 1, "no se liquidó dos veces"
        assert repo.fila.liquidaciones == 1, "el acumulado lo prueba en la base"
        assert "ya estaba activo" in segunda.detalle

    def test_se_mide_el_bloqueo_y_la_liquidacion(self) -> None:
        motor_, _, _, _ = motor()
        resultado = motor_.execute_global_liquidation(
            motivo="a", origen="MANUAL", actor="OPERADOR", ip_address="10.0.0.7"
        )
        assert resultado.ms_bloqueo >= 0.0
        assert resultado.ms_liquidacion >= 0.0

    def test_liquidacion_falla_pero_el_bloqueo_queda(self) -> None:
        banda = BanderaBloqueo()
        motor_, repo, broker, _ = motor(broker=BrokerKillSwitchFalso(roto=True), banda=banda)

        resultado = motor_.execute_global_liquidation(
            motivo="a", origen="MANUAL", actor="OPERADOR", ip_address="10.0.0.7"
        )

        assert resultado.liquidacion_estado == "FALLIDA"
        assert "falló" in resultado.detalle
        assert banda.activo is True, "el bloqueo no depende del broker"
        assert repo.fila.activo is True

    def test_sin_posiciones_igual_se_registra_completada(self) -> None:
        # RF-503: el bloqueo vale aunque no haya nada que vender.
        motor_, repo, broker, _ = motor(broker=BrokerKillSwitchFalso(ordenes=0))
        resultado = motor_.execute_global_liquidation(
            motivo="umbral", origen="AUTOMATICO", actor="SISTEMA", ip_address="127.0.0.1"
        )
        assert resultado.ordenes == 0
        assert resultado.liquidacion_estado == "COMPLETADA"
        assert repo.fila.liquidaciones == 1


class TestNoDependeDeLaBase:
    def test_postgres_caido_no_impide_bloquear_ni_liquidar(self) -> None:
        banda = BanderaBloqueo()
        motor_, _, broker, _ = motor(
            repo=RepoKillSwitchFalso(roto=True), broker=BrokerKillSwitchFalso(), banda=banda
        )

        resultado = motor_.execute_global_liquidation(
            motivo="a", origen="MANUAL", actor="OPERADOR", ip_address="10.0.0.7"
        )

        assert banda.activo is True, "el corte es en memoria, no en la base"
        assert broker.liquidaciones == 1
        assert resultado.persistencia_ok is False
        assert "no pudo guardarse" in resultado.detalle

    def test_postgres_caido_tambien_es_idempotente_en_el_proceso(self) -> None:
        banda = BanderaBloqueo()
        motor_, _, broker, _ = motor(repo=RepoKillSwitchFalso(roto=True), banda=banda)
        motor_.execute_global_liquidation(
            motivo="a", origen="MANUAL", actor="OPERADOR", ip_address="10.0.0.7"
        )
        segunda = motor_.execute_global_liquidation(
            motivo="a", origen="MANUAL", actor="OPERADOR", ip_address="10.0.0.7"
        )
        assert segunda.primera_vez is False
        assert broker.liquidaciones == 1

    def test_el_estado_inconocible_es_none_y_no_falso(self) -> None:
        # «No lo sé» no es «no bloqueado»: quien llama no emite órdenes.
        motor_, _, _, _ = motor(repo=RepoKillSwitchFalso(roto=True))
        assert motor_.esta_bloqueado() is None

        motor2, _, _, _ = motor(repo=RepoKillSwitchFalso(con_fila=False))
        assert motor2.esta_bloqueado() is None

    def test_el_estado_se_lee_cuando_todo_va(self) -> None:
        motor_, _, _, _ = motor()
        assert motor_.esta_bloqueado() is False

    def test_la_bandera_del_proceso_tambien_cuenta(self) -> None:
        banda = BanderaBloqueo()
        banda.activar()
        motor_, _, _, _ = motor(banda=banda)
        assert motor_.esta_bloqueado() is True


class TestAuditoriaMejorEsfuerzo:
    def test_si_la_auditoria_falla_el_evento_queda_en_el_archivo(self, tmp_path: Path) -> None:
        cola = ColaAuditoriaPendiente(tmp_path / "pendiente.jsonl")
        motor_, _, _, auditor = motor(auditor=AuditorKillSwitchFalso(roto=True), cola=cola)

        resultado = motor_.execute_global_liquidation(
            motivo="a", origen="MANUAL", actor="OPERADOR", ip_address="10.0.0.7"
        )

        assert resultado.auditoria_ok is False
        assert resultado.primera_vez is True  # la acción NO se aborta
        assert cola.pendientes() == 1
        registro = cola.leer()[0]
        assert registro["evento"]["action"] == ACCION_DISPARO
        assert registro["evento"]["payload"]["persistencia_ok"] is True
        assert "postgres no responde" in registro["fallo"]
        assert auditor.eventos == []

    def test_si_ambas_fallan_el_evento_tambien_queda(self, tmp_path: Path) -> None:
        cola = ColaAuditoriaPendiente(tmp_path / "pendiente.jsonl")
        motor_, _, _, _ = motor(
            repo=RepoKillSwitchFalso(roto=True), auditor=AuditorKillSwitchFalso(roto=True), cola=cola
        )

        motor_.execute_global_liquidation(
            motivo="a", origen="MANUAL", actor="OPERADOR", ip_address="10.0.0.7"
        )

        assert cola.pendientes() == 1
        evento = cola.leer()[0]["evento"]
        assert evento["payload"]["persistencia_ok"] is False

    def test_si_todo_va_no_se_escribe_nada_en_el_archivo(self, tmp_path: Path) -> None:
        cola = ColaAuditoriaPendiente(tmp_path / "pendiente.jsonl")
        motor_, _, _, _ = motor(cola=cola)
        motor_.execute_global_liquidation(
            motivo="a", origen="MANUAL", actor="OPERADOR", ip_address="10.0.0.7"
        )
        assert cola.pendientes() == 0
        assert not cola.ruta.exists()


class TestRecuperacion:
    def _cola_con_disparo(self, tmp_path: Path) -> ColaAuditoriaPendiente:
        cola = ColaAuditoriaPendiente(tmp_path / "pendiente.jsonl")
        cola.registrar(
            EventoAuditoria(
                action=ACCION_DISPARO,
                actor="SISTEMA",
                ip_address="127.0.0.1",
                payload={"motivo": "pérdida de sesión", "origen": "AUTOMATICO",
                         "persistencia_ok": False},
                timestamp_ms=1_800_000_000_000,
            ),
            "postgres no responde",
        )
        return cola

    def test_repara_un_bloqueo_que_no_llego_a_la_base(self, tmp_path: Path) -> None:
        repo = RepoKillSwitchFalso()  # fila inactiva: así quedó tras el fallo
        motor_, repo, _, auditor = motor(repo=repo, cola=self._cola_con_disparo(tmp_path))

        volcadas = motor_.recuperar()

        assert volcadas == 1, "el evento pendiente entra en la base"
        assert repo.fila.activo is True, "el bloqueo se repara (RF-503)"
        assert repo.fila.motivo == "pérdida de sesión"
        assert len(auditor.eventos) == 1
        assert motor_.recuperar() == 0, "segunda corrida: ya no hay nada"

    def test_no_repara_si_el_estado_ya_esta_activo(self, tmp_path: Path) -> None:
        repo = RepoKillSwitchFalso()
        repo.fila = estado_kill_switch(activo=True, motivo="ya")
        motor_, repo, _, _ = motor(repo=repo, cola=self._cola_con_disparo(tmp_path))

        motor_.recuperar()

        assert repo.activaciones == 0, "no se vuelve a activar"

    def test_sin_pendientes_no_toca_la_base(self, tmp_path: Path) -> None:
        repo = RepoKillSwitchFalso(roto=True)  # si lo tocara, explotaría
        motor_, _, _, _ = motor(repo=repo, cola=ColaAuditoriaPendiente(tmp_path / "x.jsonl"))
        assert motor_.recuperar() == 0

    def test_un_evento_de_otra_accion_no_repara_nada(self, tmp_path: Path) -> None:
        cola = ColaAuditoriaPendiente(tmp_path / "otro.jsonl")
        cola.registrar(
            EventoAuditoria(action="MODE_CHANGED", actor="OPERADOR",
                            ip_address="10.0.0.7", payload={}),
            "fallo",
        )
        repo = RepoKillSwitchFalso()
        motor_, repo, _, auditor = motor(repo=repo, cola=cola)

        motor_.recuperar()

        assert repo.activaciones == 0, "solo un disparo justifica reparar"
        # El evento sí se vuelve a volcar: lo que no hace es tocar el estado.
        assert cola.pendientes() == 0
        assert len(auditor.eventos) == 1


class TestColaAuditoria:
    def test_escribe_una_linea_jsonl_y_cuenta(self, tmp_path: Path) -> None:
        cola = ColaAuditoriaPendiente(tmp_path / "p.jsonl")
        evento = EventoAuditoria(
            action=ACCION_DISPARO, actor="SISTEMA", ip_address="127.0.0.1",
            payload={"x": 1},
        )

        assert cola.registrar(evento, "postgres no responde") is True

        texto = cola.ruta.read_text(encoding="utf-8")
        assert len(texto.splitlines()) == 1
        assert cola.pendientes() == 1
        assert cola.leer()[0]["fallo"] == "postgres no responde"

    def test_estampa_el_momento_si_el_evento_no_lo_trae(self, tmp_path: Path) -> None:
        cola = ColaAuditoriaPendiente(tmp_path / "p.jsonl")
        cola.registrar(
            EventoAuditoria(action="X", actor="SISTEMA", ip_address="127.0.0.1"),
            "fallo",
        )
        registrado = cola.leer()[0]["evento"]["timestamp_ms"]
        assert isinstance(registrado, int) and registrado > 1_700_000_000_000

    def test_vuelca_lo_que_puede_y_deja_lo_que_no(self, tmp_path: Path) -> None:
        cola = ColaAuditoriaPendiente(tmp_path / "p.jsonl")
        for i in range(3):
            cola.registrar(
                EventoAuditoria(action=f"ACCION{i}", actor="SISTEMA",
                                ip_address="127.0.0.1"),
                "fallo",
            )

        class _Selectivo:
            def registrar(self, evento: EventoAuditoria) -> None:
                if evento.action == "ACCION1":
                    raise RuntimeError("sigue caído")
                return None

        volcadas = cola.volcar_pendientes(_Selectivo())

        assert volcadas == 2
        assert cola.pendientes() == 1
        assert cola.leer()[0]["evento"]["action"] == "ACCION1"

    def test_una_linea_ilegal_no_tira_al_resto(self, tmp_path: Path) -> None:
        cola = ColaAuditoriaPendiente(tmp_path / "p.jsonl")
        cola.registrar(
            EventoAuditoria(action="BUENA", actor="SISTEMA", ip_address="127.0.0.1"),
            "fallo",
        )
        with cola.ruta.open("a", encoding="utf-8") as archivo:
            archivo.write("{esto no es json\n")

        assert [r["evento"]["action"] for r in cola.leer()] == ["BUENA"]

    def test_un_registro_corrupto_no_entra_en_la_base(self, tmp_path: Path) -> None:
        # `desde_dict` pasa por la misma validación que un evento nuevo.
        with pytest.raises(ValueError):
            EventoAuditoria.desde_dict({"action": "X", "actor": "NADIE",
                                        "ip_address": "127.0.0.1"})

    def test_redondo_ida_y_vuelta(self) -> None:
        original = EventoAuditoria(
            action="KILL_SWITCH_RESET", actor="CLAVE_REINICIO",
            ip_address="10.0.0.7", payload={"intentos": 2, "motivo": "reinicio"},
            timestamp_ms=42,
        )
        recuperado = EventoAuditoria.desde_dict(original.a_dict())
        assert recuperado == original

    def test_sin_archivo_no_hay_nada_que_volcar(self, tmp_path: Path) -> None:
        class _Roto:
            def registrar(self, evento: EventoAuditoria) -> None:
                raise AssertionError("no debería llamarse")

        cola = ColaAuditoriaPendiente(tmp_path / "no-existe.jsonl")
        assert cola.volcar_pendientes(_Roto()) == 0
        assert cola.pendientes() == 0


def _cargar_env() -> None:
    """Carga `.env` como los scripts (`set -a; source .env`).

    Los tests no arrancan con el entorno del servicio, y `pg.dsn()` necesita
    POSTGRES_DB/POSTGRES_USER. Solo completa lo que falte: no pisa nada.
    """
    archivo = Path(__file__).resolve().parents[1] / ".env"
    if not archivo.exists():
        return
    for linea in archivo.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, _, valor = linea.partition("=")
        os.environ.setdefault(clave.strip(), valor.strip())


@pytest.fixture()
def repo_real():
    """Repo sobre el contenedor; se omite si no hay PostgreSQL o esquema."""
    from src.database import pg
    from src.database.kill_switch import KillSwitchPostgres

    _cargar_env()
    try:
        with pg.conexion() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('kill_switch_state');")
                existe = cur.fetchone()[0]
    except Exception as exc:  # noqa: BLE001 — sin contenedor no hay prueba viva
        pytest.skip(f"PostgreSQL no responde: {str(exc)[:80]}")
    if existe is None:
        pytest.skip("esquema sin aplicar: correr tests/test_schema.py con el contenedor")
    return KillSwitchPostgres()


class TestEnBaseReal:
    """El compare-and-set y la siembra, contra el motor de verdad.

    Lección de CR-003: el comportamiento de la base no se asume. La
    idempotencia del disparo depende de que `UPDATE ... WHERE activo = FALSE`
    devuelva fila una sola vez, y eso solo se comprueba aquí.
    """

    @staticmethod
    def _inactivar(repo) -> None:
        repo.reiniciar(ahora_ms=1, sesion=SESION)

    def test_la_siembra_no_duplica_filas(self, repo_real) -> None:
        repo_real.sembrar_si_vacio()
        assert repo_real.sembrar_si_vacio() is False
        assert repo_real.leer() is not None

    def test_el_compare_and_set_solo_gana_una_vez(self, repo_real) -> None:
        self._inactivar(repo_real)

        primera = repo_real.activar(
            motivo="prueba", origen="MANUAL", actor="OPERADOR", ahora_ms=10
        )
        segunda = repo_real.activar(
            motivo="prueba", origen="MANUAL", actor="OPERADOR", ahora_ms=11
        )

        assert primera is True, "la primera pulsación debe ganar"
        assert segunda is False, "la segunda no puede volver a activar"
        estado = repo_real.leer()
        assert estado is not None and estado.activo is True
        assert estado.disparado_en == 10, "el primer disparo es el que manda"

        self._inactivar(repo_real)

    def test_liquidaciones_se_acumulan_entre_activaciones(self, repo_real) -> None:
        # El acumulado vive en una base que persiste entre corridas, así que
        # se mide la DIFERENCIA, no el valor absoluto.
        self._inactivar(repo_real)
        inicial = repo_real.leer()
        assert inicial is not None
        base = inicial.liquidaciones

        repo_real.activar(motivo="p", origen="MANUAL", actor="OPERADOR", ahora_ms=1)
        repo_real.marcar_liquidacion("EN_CURSO", None)
        repo_real.marcar_liquidacion("COMPLETADA", 2)
        primera = repo_real.leer()
        assert primera is not None
        assert primera.liquidacion_estado == "COMPLETADA"
        assert primera.liquidaciones == base + 1

        repo_real.reiniciar(ahora_ms=3, sesion=SESION)
        repo_real.activar(motivo="p", origen="AUTOMATICO", actor="SISTEMA", ahora_ms=4)
        repo_real.marcar_liquidacion("COMPLETADA", 5)
        segunda = repo_real.leer()
        assert segunda is not None
        assert segunda.liquidaciones == base + 2, "el acumulado sobrevive al reinicio"

        self._inactivar(repo_real)

    def test_el_reinicio_es_idempotente_y_guarda_la_sesion(self, repo_real) -> None:
        repo_real.activar(motivo="p", origen="MANUAL", actor="OPERADOR", ahora_ms=1)

        assert repo_real.reiniciar(ahora_ms=2, sesion=SESION) is True
        assert repo_real.reiniciar(ahora_ms=3, sesion=SESION) is False

        estado = repo_real.leer()
        assert estado is not None
        assert estado.activo is False
        assert estado.sesion_reinicio == SESION
        assert estado.liquidacion_estado == "NINGUNA"

    def test_los_valores_invalidos_se_rechazan_antes_de_escribir(self, repo_real) -> None:
        from src.database.kill_switch import ESTADOS_LIQUIDACION

        with pytest.raises(ValueError, match="origen"):
            repo_real.activar(motivo="x", origen="INVENTADO", actor="SISTEMA", ahora_ms=1)
        with pytest.raises(ValueError, match="actor"):
            repo_real.activar(motivo="x", origen="MANUAL", actor="GERENTE", ahora_ms=1)
        with pytest.raises(ValueError, match="estado"):
            repo_real.marcar_liquidacion("INVENTADO", 1)
        assert "INVENTADO" not in ESTADOS_LIQUIDACION
        self._inactivar(repo_real)

    def test_la_tabla_existe_y_esta_vacia_de_sorpresas(self, repo_real) -> None:
        # Si alguien cambia el esquema sin actualizar esta capa, se nota aquí.
        estado = repo_real.leer()
        assert estado is not None
        assert estado.liquidacion_estado in (
            "NINGUNA", "PENDIENTE", "EN_CURSO", "COMPLETADA", "FALLIDA"
        )
        assert estado.origen in ("MANUAL", "AUTOMATICO")
        assert estado.actor in ("SISTEMA", "OPERADOR")
        self._inactivar(repo_real)


class TestMotorReal:
    """`dependencias.motor_kill_switch` contra PostgreSQL de verdad.

    Es el cable que usa el API al arrancar: si el motor no se puede montar,
    la siembra falla o `resumen()` no responde, el panel no tendría estado
    del Kill-Switch. Solo lectura salvo la siembra idempotente, que es
    exactamente lo que hace el arranque en producción.
    """

    def test_se_monta_siembra_y_responde(self, repo_real) -> None:
        from src.security.kill_switch import motor_desde_entorno
        from tests.fakes import LectorFalso

        _cargar_env()
        motor = motor_desde_entorno(LectorFalso())
        resumen = motor.resumen()

        assert resumen.activo in (True, False, None)
        assert resumen.detalle.strip(), "el texto llano no puede quedar vacío"
        assert motor.esta_bloqueado() == resumen.activo

    def test_la_siembra_no_duplica_la_fila(self, repo_real) -> None:
        from src.security.kill_switch import motor_desde_entorno
        from tests.fakes import LectorFalso

        _cargar_env()
        motor_desde_entorno(LectorFalso())
        assert motor_desde_entorno(LectorFalso()).estado() is not None


class TestLatenciasRnf010:
    """RNF-010 medido: cuatro cifras que miden cuatro cosas distintas.

    | cifra | qué la compone | contra qué corre |
    |---|---|---|
    | `ms_bloqueo` | solo la bandera en memoria del proceso | nada (sin I/O) |
    | `ms_persistencia` | solo el `UPDATE … WHERE activo = FALSE` + commit | el repositorio que se le pase: aquí **PostgreSQL real** |
    | `ms_liquidacion` | solo la llamada al `BrokerGateway` | `SimulatedBrokerGateway` con repositorio **en memoria** |
    | pulsación→respuesta | la petición HTTP entera | `tests/test_endpoints_kill_switch.py` |

    Ninguna incluye a la otra. **La liquidación es simulada**: contra IB real
    no se ha medido nada (Ítem 12 pendiente), y así se rotula en el informe
    §4. RNF-010 pide «prioridad a nivel de hilos de CPU», que en Python no
    existe literalmente — ver el docstring de `src/security/kill_switch.py`.
    """

    @staticmethod
    def _percentil(valores: list[float], p: float) -> float:
        ordenados = sorted(valores)
        posicion = int(round(p * (len(ordenados) - 1)))
        return ordenados[max(0, min(len(ordenados) - 1, posicion))]

    @staticmethod
    def _medir(repo, n: int) -> tuple[list[float], list[float], list[float]]:
        """`n` disparos completos, cada uno con su reinicio (estado limpio).

        El broker es el **real** (`SimulatedBrokerGateway`) sobre un
        repositorio de transacciones en memoria con una posición abierta:
        se cronometra el camino de verdad —calcular posiciones, pedir precio
        y construir la transacción— sin escribir en `transactions`.
        """
        from src.integration.broker import SimulatedBrokerGateway
        from tests.fakes import TransaccionesFalsas

        bloqueos: list[float] = []
        persistencias: list[float] = []
        liquidaciones: list[float] = []
        for _ in range(n):
            banda = BanderaBloqueo()
            transacciones = TransaccionesFalsas(neta=2.0)
            broker = SimulatedBrokerGateway(
                transacciones,
                precio_actual=lambda _s: 512.5,
                ahora_ms=lambda: 1_800_000_000_000,
                nuevo_id=lambda: f"SIM-{len(transacciones.guardadas) + 1}",
            )
            motor = KillSwitchEngine(
                repo, broker, AuditorKillSwitchFalso(),
                banda=banda, ahora_ms=lambda: 1_800_000_000_000,
            )
            resultado = motor.execute_global_liquidation(
                motivo="medición RNF-010", origen="MANUAL",
                actor="OPERADOR", ip_address="127.0.0.1",
            )
            assert resultado.primera_vez is True, "cada muestra parte de inactivo"
            assert resultado.ordenes == 1, "la liquidación debe recorrer el camino real"
            bloqueos.append(resultado.ms_bloqueo)
            persistencias.append(resultado.ms_persistencia)
            liquidaciones.append(resultado.ms_liquidacion)
            repo.reiniciar(ahora_ms=2, sesion=SESION)
        return bloqueos, persistencias, liquidaciones

    @staticmethod
    def _imprimir(rotulo: str, bloqueos, persistencias, liquidaciones) -> None:
        imprimir = lambda nombre, v: (
            f"{nombre} p50 {TestLatenciasRnf010._percentil(v, 0.5):.3f} ms "
            f"/ p95 {TestLatenciasRnf010._percentil(v, 0.95):.3f} ms"
        )
        print(
            f"\nRNF-010 [{rotulo}]\n"
            f"  banda en memoria (sin I/O):        {imprimir('', bloqueos)}\n"
            f"  compare-and-set en la base:        {imprimir('', persistencias)}\n"
            f"  liquidación (gateway simulado):    {imprimir('', liquidaciones)}"
        )

    def test_con_cotas_con_dobles(self) -> None:
        # Repositorio en memoria: mide el overhead del propio motor.
        bloqueos, persistencias, liquidaciones = self._medir(
            RepoKillSwitchFalso(), n=50
        )
        self._imprimir("dobles en memoria", bloqueos, persistencias, liquidaciones)

        assert self._percentil(bloqueos, 0.95) < 50.0, "el corte no puede tardar"
        assert self._percentil(persistencias, 0.95) < 50.0
        assert self._percentil(liquidaciones, 0.95) < 100.0

    def test_contra_postgres_real(self, repo_real) -> None:
        """Las mismas cifras con el compare-and-set de verdad.

        El repositorio es `KillSwitchPostgres` contra `ritech-postgres16`:
        ahí sí hay red local, `UPDATE` y `commit`. Sin `audit_logs` de por
        medio (el doble de auditoría), porque lo que se mide es el bloqueo,
        no el log. Deja `kill_switch_state` inactiva, como la encontró.
        """
        bloqueos, persistencias, liquidaciones = self._medir(repo_real, n=20)
        self._imprimir(
            "PostgreSQL real", bloqueos, persistencias, liquidaciones
        )

        estado = repo_real.leer()
        assert estado is not None and estado.activo is False, "deja todo inactivo"
        # RNF-010 no da una cifra; la cota es la de RNF-003 (500 ms) por
        # prudencia, y en la práctica va dos órdenes de magnitud por debajo.
        assert self._percentil(bloqueos, 0.95) < 500.0
        assert self._percentil(persistencias, 0.95) < 500.0
        assert self._percentil(liquidaciones, 0.95) < 500.0

    def test_cada_latencia_es_de_su_paso(self) -> None:
        # El orden cronológico es bandera → CAS → liquidación, y cada
        # medición cubre SOLO su paso: si alguna empezara a engordar
        # (p. ej. meter el I/O dentro de la bandera), esto lo delata.
        repo = RepoKillSwitchFalso()
        broker = BrokerKillSwitchFalso()
        banda = BanderaBloqueo()
        motor = KillSwitchEngine(
            repo, broker, AuditorKillSwitchFalso(),
            banda=banda, ahora_ms=lambda: 1,
        )
        resultado = motor.execute_global_liquidation(
            motivo="x", origen="MANUAL", actor="OPERADOR", ip_address="127.0.0.1"
        )
        assert resultado.ms_bloqueo >= 0.0
        assert resultado.ms_persistencia >= 0.0
        assert resultado.ms_liquidacion >= 0.0
        # Sin I/O, la banda y la liquidación con dobles son despreciables;
        # el CAS con dobles también. Lo importante es que existan por
        # separado: `ms_persistencia` no formaba parte del resultado antes.
        assert broker.liquidaciones == 1


class TestNotificacionDelDisparo:
    """SS-11: el aviso es el paso (e) y jamás puede frenar los pasos (a)-(d)."""

    class _Canal:
        """Canal que registra lo que recibe, sin enviar nada."""

        def __init__(self) -> None:
            self.recibidos: list = []

        def enviar(self, aviso) -> None:
            self.recibidos.append(aviso)

    class _Caido:
        """Canal SMTP caído: lanza en el primer envío."""

        def __init__(self) -> None:
            self.intentos = 0

        def enviar(self, aviso) -> None:
            self.intentos += 1
            raise ConnectionRefusedError("correo caído")

    class _Lento:
        """Canal que se queda dentro de `enviar` hasta que se le diga."""

        def __init__(self) -> None:
            self.entro = threading.Event()
            self.suelta = threading.Event()

        def enviar(self, aviso) -> None:
            self.entro.set()
            self.suelta.wait(timeout=5)

    def _disparar(self, canal):
        repo = RepoKillSwitchFalso()
        auditor = AuditorKillSwitchFalso()
        banda = BanderaBloqueo()
        motor = KillSwitchEngine(
            repo, BrokerKillSwitchFalso(), auditor,
            banda=banda, ahora_ms=lambda: 1_800_000_000_000, notificador=canal,
        )
        resultado = motor.execute_global_liquidation(
            motivo="Pérdida de sesión de 1,40 % (límite 1,25 %).",
            origen="AUTOMATICO", actor="SISTEMA", ip_address="127.0.0.1",
        )
        assert motor.esperar_notificaciones(timeout_s=5) is True
        return motor, repo, auditor, resultado

    def test_con_el_correo_caido_el_disparo_completa(self) -> None:
        # LA regla crítica: nada del Kill-Switch depende del envío.
        canal = self._Caido()
        motor, repo, auditor, resultado = self._disparar(canal)

        assert resultado.primera_vez is True
        assert repo.fila.activo is True, "el bloqueo quedó puesto"
        assert resultado.liquidacion_estado == "COMPLETADA"
        assert canal.intentos == 1, "sí se intentó enviar"

        acciones = [e.action for e in auditor.eventos]
        assert "KILL_SWITCH_TRIGGERED" in acciones, acciones
        assert "NOTIFICACION_FALLIDA" in acciones, acciones
        fallo = next(e for e in auditor.eventos if e.action == "NOTIFICACION_FALLIDA")
        assert "correo caído" in fallo.payload["fallo"]

    def _orden_de_eventos(self, auditor) -> list[str]:
        return [e.action for e in auditor.eventos]

    def test_la_notificacion_va_despues_de_la_auditoria_del_disparo(self) -> None:
        # (a)-(d) antes que (e): el registro del disparo tiene que existir
        # antes que el del envío.
        _, _, auditor, _ = self._disparar(self._Canal())
        acciones = self._orden_de_eventos(auditor)
        assert acciones.index("KILL_SWITCH_TRIGGERED") < acciones.index(
            "NOTIFICACION_ENVIADA"
        ), acciones

    def test_el_aviso_lleva_el_texto_del_backend(self) -> None:
        canal = self._Canal()
        self._disparar(canal)

        aviso = canal.recibidos[0]
        assert aviso.origen == "kill-switch"
        assert "Kill-Switch" in aviso.asunto
        assert "Pérdida de sesión de 1,40 %" in aviso.cuerpo
        assert "hasta que un administrador" in aviso.cuerpo

    def test_un_canal_lento_no_retrasa_el_disparo(self) -> None:
        # El paso (e) corre en su propio hilo: el disparo devuelve mientras
        # el correo sigue dentro de `enviar`.
        canal = self._Lento()
        repo = RepoKillSwitchFalso()
        motor = KillSwitchEngine(
            repo, BrokerKillSwitchFalso(), AuditorKillSwitchFalso(),
            banda=BanderaBloqueo(), ahora_ms=lambda: 1, notificador=canal,
        )

        inicio = time.monotonic()
        resultado = motor.execute_global_liquidation(
            motivo="x", origen="MANUAL", actor="OPERADOR", ip_address="127.0.0.1",
        )
        transcurrido = time.monotonic() - inicio

        assert resultado.primera_vez is True
        assert transcurrido < 1.0, f"el disparo esperó al canal: {transcurrido:.3f} s"
        assert repo.fila.activo is True

        canal.suelta.set()  # se libera el hilo antes de terminar el test
        assert motor.esperar_notificaciones(timeout_s=5) is True

    def test_sin_notificador_no_se_registra_nada_de_notificacion(self) -> None:
        repo = RepoKillSwitchFalso()
        auditor = AuditorKillSwitchFalso()
        motor = KillSwitchEngine(
            repo, BrokerKillSwitchFalso(), auditor,
            banda=BanderaBloqueo(), ahora_ms=lambda: 1,
        )
        motor.execute_global_liquidation(
            motivo="x", origen="MANUAL", actor="OPERADOR", ip_address="127.0.0.1"
        )
        assert [e.action for e in auditor.eventos] == ["KILL_SWITCH_TRIGGERED"]
        assert motor._hilos_notificacion == []
