"""`/api/kill-switch` — las tres rutas, con el motor REAL sobre dobles.

El motor, el repositorio y la auditoría son los de producción; lo que se
falsifica es PostgreSQL, el broker y el archivo pendiente. Así se prueba el
comportamiento completo del endpoint —incluida la lectura estricta del
reinicio— sin contenedor.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from datetime import date

from src.security import clave_reinicio
from src.security.auditoria_pendiente import ColaAuditoriaPendiente
from src.security.kill_switch import BanderaBloqueo, KillSwitchEngine
from tests.fakes import (
    AHORA_EN_SESION,
    INTERVALO_MS,
    AuditorKillSwitchFalso,
    BrokerKillSwitchFalso,
    RepoKillSwitchFalso,
    cliente_de_prueba,
    estado_de_prueba,
    estado_kill_switch,
    serie_sesion,
)

AHORA_MS = 1_800_000_000_000
#: Sesión de referencia para reiniciar entre muestras (fecha ET).
SESION_DIA = date(2026, 11, 3)
CLAVE = "clave-de-reinicio-correcta"
HASH = clave_reinicio.generar_hash(CLAVE, iteraciones=1_000)


def _velas(hace_minutos: float = 5.0, n: int = 30) -> list:
    """Sesión cuya última vela cierra `hace_minutos` antes de la hora fija."""
    ahora_ms = int(AHORA_EN_SESION.timestamp() * 1000)
    fin = ahora_ms - int(hace_minutos * 60_000)
    return serie_sesion(fin - (n - 1) * INTERVALO_MS, n)


def montar(
    *,
    activo: bool = False,
    auditor_roto: bool = False,
    intentos: int = 0,
    ultimo_intento: int = 0,
    con_fila: bool = True,
    cola: ColaAuditoriaPendiente | None = None,
) -> SimpleNamespace:
    """Cliente de prueba + las piezas del motor, para mirarlas después.

    La cola pendiente por defecto va a un directorio temporal: NUNCA a
    `logs/auditoria-pendiente.jsonl`, que es el archivo real. Un evento
    escrito ahí por un test haría que `motor_desde_entorno` «repusiera» un
    bloqueo en la base de verdad al arrancar en otro test.
    """
    repo = RepoKillSwitchFalso(con_fila=con_fila)
    repo.fila = estado_kill_switch(
        activo=activo,
        motivo="prueba" if activo else "Sin disparo",
        intentos_reinicio=intentos,
        ultimo_intento_en=ultimo_intento,
    )
    banda = BanderaBloqueo()
    broker = BrokerKillSwitchFalso()
    broker.banda = banda  # para poder comprobar el orden (a) → (c)
    auditor = AuditorKillSwitchFalso(roto=auditor_roto)
    if cola is None:
        cola = ColaAuditoriaPendiente(Path(tempfile.mkdtemp()) / "pendiente.jsonl")
    motor = KillSwitchEngine(
        repo, broker, auditor, cola=cola, banda=banda, ahora_ms=lambda: AHORA_MS
    )
    estado = estado_de_prueba(motor=motor, velas=_velas())
    return SimpleNamespace(
        cliente=cliente_de_prueba(estado),
        repo=repo, broker=broker, auditor=auditor, banda=banda, motor=motor,
        cola=cola, estado=estado,
    )


class TestConsulta:
    def test_sin_disparo_informa_desactivado(self) -> None:
        cuerpo = montar().cliente.get("/api/kill-switch").json()
        assert cuerpo["activo"] is False
        assert cuerpo["detalle"] == "Sin bloqueo: el Kill-Switch está desactivado."
        assert cuerpo["liquidaciones"] == 0

    def test_con_disparo_informa_el_estado_completo(self) -> None:
        cuerpo = montar(activo=True).cliente.get("/api/kill-switch").json()
        assert cuerpo["activo"] is True
        assert cuerpo["motivo"] == "prueba"
        assert "Kill-Switch ACTIVO" in cuerpo["detalle"]
        assert "hasta un reinicio autorizado" in cuerpo["detalle"]

    def test_sin_motor_el_api_no_afirma_nada(self) -> None:
        # `motor_kill_switch = None` no es «desactivado».
        estado = estado_de_prueba()
        estado.motor_kill_switch = None
        respuesta = cliente_de_prueba(estado).get("/api/kill-switch")
        assert respuesta.status_code == 503
        assert "no está configurado" in respuesta.json()["mensaje"]


class TestDisparo:
    def test_dispara_sin_clave(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # RF-501: la emergencia no pide credencial.
        monkeypatch.delenv(clave_reinicio.VARIABLE, raising=False)
        piezas = montar()

        respuesta = piezas.cliente.post("/api/kill-switch")

        assert respuesta.status_code == 200
        cuerpo = respuesta.json()
        assert cuerpo["activo"] is True
        assert cuerpo["primera_vez"] is True
        assert cuerpo["ms_bloqueo"] >= 0
        assert cuerpo["persistencia_ok"] is True
        assert cuerpo["auditoria_ok"] is True

    def test_segunda_pulsacion_no_liquid_dos_veces(self) -> None:
        piezas = montar()
        primera = piezas.cliente.post("/api/kill-switch").json()
        segunda = piezas.cliente.post("/api/kill-switch").json()

        assert primera["primera_vez"] is True
        assert segunda["primera_vez"] is False
        assert piezas.broker.liquidaciones == 1
        assert "ya estaba activo" in segunda["detalle"]

    def test_queda_auditoria_con_ip_y_actor(self) -> None:
        piezas = montar()
        piezas.cliente.post("/api/kill-switch")

        assert len(piezas.auditor.eventos) == 1
        evento = piezas.auditor.eventos[0]
        assert evento.action == "KILL_SWITCH_TRIGGERED"
        assert evento.actor == "OPERADOR"
        assert evento.ip_address, "sin IP el registro no prueba nada (§5.3)"
        assert evento.payload is not None
        assert evento.payload["origen"] == "MANUAL"
        assert evento.payload["ordenes_simuladas"] == 1

    def test_el_bloqueo_se_pone_antes_de_liquidar(self) -> None:
        piezas = montar()
        piezas.cliente.post("/api/kill-switch")
        assert piezas.broker.bloqueo_puesto_al_liquidar is True
        assert piezas.banda.activo is True

    def test_una_base_caida_no_impide_disparar(self) -> None:
        piezas = montar(con_fila=False)
        # `leer()` devuelve None pero `activar()` del doble sigue funcionando
        # sobre la fila interna; lo que importa es que la petición no falle.
        respuesta = piezas.cliente.post("/api/kill-switch")
        assert respuesta.status_code == 200
        assert piezas.banda.activo is True

    def test_si_falla_la_auditoria_el_disparo_no_se_aborta(self) -> None:
        piezas = montar(auditor_roto=True)
        respuesta = piezas.cliente.post("/api/kill-switch")

        assert respuesta.status_code == 200
        assert respuesta.json()["auditoria_ok"] is False
        assert piezas.banda.activo is True, "la acción NO depende de la auditoría"
        # ...pero el evento no se pierde: queda en la cola para volcarlo.
        assert piezas.cola.pendientes() == 1
        assert piezas.cola.leer()[0]["evento"]["action"] == "KILL_SWITCH_TRIGGERED"


class TestReinicio:
    def test_sin_hash_configurado_esta_deshabilitado(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(clave_reinicio.VARIABLE, raising=False)
        piezas = montar(activo=True)

        respuesta = piezas.cliente.post("/api/kill-switch/reset")

        assert respuesta.status_code == 503
        assert clave_reinicio.VARIABLE in respuesta.json()["mensaje"]
        assert piezas.repo.fila.activo is True, "sigue bloqueado"

    def test_clave_incorrecta_suma_intento_y_no_reinicia(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(clave_reinicio.VARIABLE, HASH)
        piezas = montar(activo=True)

        respuesta = piezas.cliente.post(
            "/api/kill-switch/reset", headers={"X-Kill-Switch-Key": "otra-clave"}
        )

        assert respuesta.status_code == 401
        assert "incorrecta" in respuesta.json()["mensaje"]
        assert piezas.repo.fila.intentos_reinicio == 1
        assert piezas.repo.fila.activo is True
        assert piezas.auditor.eventos == [], "un intento fallido no se audita como reinicio"

    def test_clave_correcta_reinicia_y_limpia_la_bandera(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(clave_reinicio.VARIABLE, HASH)
        piezas = montar(activo=True)
        piezas.banda.activar()

        respuesta = piezas.cliente.post(
            "/api/kill-switch/reset", headers={"X-Kill-Switch-Key": CLAVE}
        )

        assert respuesta.status_code == 200
        cuerpo = respuesta.json()
        assert cuerpo["reiniciado"] is True
        assert "vuelve a operar" in cuerpo["detalle"]
        assert piezas.repo.fila.activo is False
        assert piezas.banda.activo is False
        assert piezas.repo.reinicio_hizo is True

    def test_el_reinicio_se_audita_con_actor_de_clave(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(clave_reinicio.VARIABLE, HASH)
        piezas = montar(activo=True)
        piezas.cliente.post(
            "/api/kill-switch/reset", headers={"X-Kill-Switch-Key": CLAVE}
        )

        assert len(piezas.auditor.eventos) == 1
        evento = piezas.auditor.eventos[0]
        assert evento.action == "KILL_SWITCH_RESET"
        assert evento.actor == "CLAVE_REINICIO", "sin identidad personal"
        assert evento.ip_address

    def test_reiniciar_dos_veces_es_idempotente(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(clave_reinicio.VARIABLE, HASH)
        piezas = montar(activo=True)
        piezas.cliente.post(
            "/api/kill-switch/reset", headers={"X-Kill-Switch-Key": CLAVE}
        )
        segunda = piezas.cliente.post(
            "/api/kill-switch/reset", headers={"X-Kill-Switch-Key": CLAVE}
        )

        assert segunda.status_code == 200
        assert segunda.json()["reiniciado"] is False
        assert "ya estaba desactivado" in segunda.json()["detalle"]
        assert len(piezas.auditor.eventos) == 1, "solo el primer reinicio se registra"

    def test_sin_clave_enviada_no_reinicia(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(clave_reinicio.VARIABLE, HASH)
        piezas = montar(activo=True)

        respuesta = piezas.cliente.post("/api/kill-switch/reset")

        assert respuesta.status_code == 401
        assert piezas.repo.fila.activo is True

    @pytest.mark.parametrize("maximos", [2, 5])
    def test_llega_al_limite_de_intentos_y_bloquea(
        self, monkeypatch: pytest.MonkeyPatch, maximos: int
    ) -> None:
        monkeypatch.setenv(clave_reinicio.VARIABLE, HASH)
        monkeypatch.setenv("KILL_SWITCH_MAX_ATTEMPTS", str(maximos))
        piezas = montar(activo=True)
        headers = {"X-Kill-Switch-Key": "mala"}

        for i in range(maximos):
            respuesta = piezas.cliente.post("/api/kill-switch/reset", headers=headers)
            assert respuesta.status_code == 401, f"intento {i + 1}"

        bloqueado = piezas.cliente.post("/api/kill-switch/reset", headers=headers)
        assert bloqueado.status_code == 429
        assert "Demasiados intentos" in bloqueado.json()["mensaje"]
        assert piezas.repo.fila.intentos_reinicio == maximos

        # Ni siquiera la clave CORRECTA pasa mientras dure la ventana.
        correcta = piezas.cliente.post(
            "/api/kill-switch/reset", headers={"X-Kill-Switch-Key": CLAVE}
        )
        assert correcta.status_code == 429

    def test_pasar_la_ventana_de_tiempo_deja_volver_a_intentarlo(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import time

        monkeypatch.setenv(clave_reinicio.VARIABLE, HASH)
        monkeypatch.setenv("KILL_SWITCH_MAX_ATTEMPTS", "1")
        monkeypatch.setenv("KILL_SWITCH_VENTANA_INTENTOS_S", "60")
        # El último intento, 2 minutos antes de AHORA REAL (el endpoint usa
        # el reloj del sistema, no el reloj de prueba del motor).
        viejo = int(time.time() * 1000) - 120_000
        piezas = montar(activo=True, intentos=3, ultimo_intento=viejo)

        respuesta = piezas.cliente.post(
            "/api/kill-switch/reset", headers={"X-Kill-Switch-Key": CLAVE}
        )

        assert respuesta.status_code == 200
        assert respuesta.json()["reiniciado"] is True

    def test_sin_auditoria_no_se_reinicia(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Lectura estricta de SDD §7 regla 3: el log falla ⇒ no hay acción.
        monkeypatch.setenv(clave_reinicio.VARIABLE, HASH)
        piezas = montar(activo=True, auditor_roto=True)

        respuesta = piezas.cliente.post(
            "/api/kill-switch/reset", headers={"X-Kill-Switch-Key": CLAVE}
        )

        assert respuesta.status_code == 503
        assert "No se ejecutó" in respuesta.json()["mensaje"]
        assert piezas.repo.fila.activo is True, "sigue bloqueado"
        assert piezas.repo.reinicio_hizo is False

    def test_estado_ilegible_no_reinicia(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(clave_reinicio.VARIABLE, HASH)
        piezas = montar(activo=True)
        piezas.repo.roto = True

        respuesta = piezas.cliente.post(
            "/api/kill-switch/reset", headers={"X-Kill-Switch-Key": CLAVE}
        )

        assert respuesta.status_code == 503
        assert piezas.banda.activo is False


class TestEstadoEnStatus:
    def test_kill_switch_activo_pinta_rojo_y_no_operable(self) -> None:
        piezas = montar(activo=True)
        cuerpo = piezas.cliente.get("/api/status").json()

        assert cuerpo["estado"] == "ROJO"
        assert "Kill-Switch ACTIVO" in cuerpo["motivo"]
        assert cuerpo["kill_switch"]["activo"] is True
        assert cuerpo["senal"]["operable"] is False
        assert "Kill-Switch ACTIVO" in cuerpo["senal"]["motivo"]

    def test_kill_switch_desactivado_no_altera_el_estado(self) -> None:
        piezas = montar(activo=False)
        cuerpo = piezas.cliente.get("/api/status").json()

        assert cuerpo["estado"] == "VERDE"
        assert cuerpo["kill_switch"]["activo"] is False
        assert cuerpo["senal"]["operable"] is False  # STABLE por defecto

    def test_estado_no_verificado_es_amarillo_y_no_operable(self) -> None:
        piezas = montar()
        piezas.repo.roto = True  # PostgreSQL caído
        cuerpo = piezas.cliente.get("/api/status").json()

        assert cuerpo["estado"] == "AMARILLO"
        assert "no se pudo verificar el estado del Kill-Switch" in cuerpo["motivo"]
        assert cuerpo["kill_switch"]["activo"] is None
        assert cuerpo["senal"]["operable"] is False
        assert any("Kill-Switch" in aviso for aviso in cuerpo["avisos"])

    def test_sin_motor_configurado_tambien_bloquea(self) -> None:
        estado = estado_de_prueba()
        estado.motor_kill_switch = None
        cuerpo = cliente_de_prueba(estado).get("/api/status").json()

        assert cuerpo["estado"] == "AMARILLO"
        assert cuerpo["kill_switch"]["activo"] is None
        assert cuerpo["senal"]["operable"] is False


class TestLatenciaDeLaPulsacion:
    """RNF-010 desde el cliente: pulsación → respuesta del endpoint.

    El número del motor (banda + CAS + liquidación) está en
    `tests/test_kill_switch.py::TestLatenciasRnf010`; aquí se mide lo que
    ve quien pulsa el botón: la petición HTTP entera con el motor real
    sobre dobles de base de datos.
    """

    @staticmethod
    def _percentil(valores: list[float], p: float) -> float:
        ordenados = sorted(valores)
        posicion = int(round(p * (len(ordenados) - 1)))
        return ordenados[max(0, min(len(ordenados) - 1, posicion))]

    def test_pulsacion_a_respuesta(self) -> None:
        import time

        piezas = montar()
        muestras: list[float] = []
        for _ in range(30):
            inicio = time.perf_counter()
            respuesta = piezas.cliente.post("/api/kill-switch")
            muestras.append((time.perf_counter() - inicio) * 1000)
            assert respuesta.status_code == 200
            assert respuesta.json()["primera_vez"] is True
            # Cada muestra parte de «inactivo»: se restablece el doble.
            piezas.repo.fila = estado_kill_switch()
            piezas.banda.liberar()

        p50 = self._percentil(muestras, 0.5)
        p95 = self._percentil(muestras, 0.95)
        print(
            f"\nRNF-010 (HTTP, dobles): pulsación→respuesta "
            f"p50 {p50:.2f} ms / p95 {p95:.2f} ms"
        )
        assert p95 < 250.0, "la pulsación no puede tardar más que esto"


class TestLatenciaConPostgresReal:
    """Pulsación→respuesta con el **compare-and-set contra PostgreSQL real**.

    Es la única cifra que une las dos mitades de RNF-010: lo que ve quien
    pulsa el botón, con la persistencia de verdad (connect + UPDATE + commit
    en `ritech-postgres16`), el CAS de seguridad de la liquidación y el
    `resumen()` que se devuelve al panel.

    **Excluido a propósito:** el `INSERT` de `audit_logs` (una medición no
    es un evento, y `audit_logs` es inmutable: no habría forma de limpiarla)
    — la auditoría va a un doble. La liquidación es **simulada**
    (`SimulatedBrokerGateway`); contra IB real no se ha medido (Ítem 12).
    """

    @staticmethod
    def _percentil(valores: list[float], p: float) -> float:
        ordenados = sorted(valores)
        posicion = int(round(p * (len(ordenados) - 1)))
        return ordenados[max(0, min(len(ordenados) - 1, posicion))]

    @pytest.fixture()
    def cliente_real(self):
        from src.database import pg
        from src.database.kill_switch import KillSwitchPostgres
        from src.integration.broker import SimulatedBrokerGateway
        from tests.fakes import TransaccionesFalsas
        from tests.test_kill_switch import _cargar_env

        _cargar_env()
        try:
            with pg.conexion() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT to_regclass('kill_switch_state');")
                    existe = cur.fetchone()[0]
        except Exception as exc:  # noqa: BLE001 — sin contenedor no hay prueba viva
            pytest.skip(f"PostgreSQL no responde: {str(exc)[:80]}")
        if existe is None:
            pytest.skip("esquema sin aplicar: correr tests/test_schema.py")

        repo = KillSwitchPostgres()
        transacciones = TransaccionesFalsas(neta=2.0)
        banda = BanderaBloqueo()
        motor = KillSwitchEngine(
            repo,
            SimulatedBrokerGateway(
                transacciones,
                precio_actual=lambda _s: 512.5,
                ahora_ms=lambda: AHORA_MS,
                nuevo_id=lambda: f"SIM-{len(transacciones.guardadas) + 1}",
            ),
            AuditorKillSwitchFalso(),
            banda=banda,
            ahora_ms=lambda: AHORA_MS,
        )
        return SimpleNamespace(
            cliente=cliente_de_prueba(estado_de_prueba(motor=motor)),
            repo=repo,
            banda=banda,
        )

    def test_pulsacion_a_respuesta_contra_postgres_real(self, cliente_real) -> None:
        import time

        # Arranca desde «inactivo» pase lo que pase con el orden de los
        # tests: la primera muestra exige `primera_vez = True` y una base
        # heredada con el bloqueo activo la haría fallar sin ser un defecto.
        cliente_real.repo.reiniciar(ahora_ms=AHORA_MS, sesion=SESION_DIA)
        cliente_real.banda.liberar()

        muestras: list[float] = []
        for _ in range(10):
            inicio = time.perf_counter()
            respuesta = cliente_real.cliente.post("/api/kill-switch")
            muestras.append((time.perf_counter() - inicio) * 1000)
            assert respuesta.status_code == 200
            cuerpo = respuesta.json()
            assert cuerpo["primera_vez"] is True
            assert cuerpo["persistencia_ok"] is True, "el CAS sí fue a la base"
            assert cuerpo["ms_persistencia"] > 0, "la medición del CAS existe"
            # Cada muestra parte de inactivo.
            cliente_real.repo.reiniciar(ahora_ms=AHORA_MS, sesion=SESION_DIA)
            cliente_real.banda.liberar()

        p50 = self._percentil(muestras, 0.5)
        p95 = self._percentil(muestras, 0.95)
        print(
            f"\nRNF-010 (HTTP + PostgreSQL real, sin auditoría): "
            f"pulsación→respuesta p50 {p50:.2f} ms / p95 {p95:.2f} ms"
        )
        assert cliente_real.repo.leer().activo is False, "deja todo inactivo"
        assert p95 < 500.0, "por debajo de la cota de RNF-003"
