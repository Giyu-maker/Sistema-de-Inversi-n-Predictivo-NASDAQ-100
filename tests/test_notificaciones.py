"""Notificaciones locales (SS-11, SRS §5.4) — canales, mejor esfuerzo y avisos.

El SMTP se prueba contra un doble de `smtplib`: aquí importa la
**política** (qué mensaje sale, con qué remetente, si se negocia TLS, qué
pasa si falla) y no la red. El SMS simulado se comprueba leyendo la
bandeja que escribe, porque esa bandeja ES el entregable.

La regla crítica tiene su prueba en `tests/test_kill_switch.py`: el
Kill-Switch completa con el SMTP caído.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from src.integration.notificaciones import (
    ACCION_ENVIADA,
    ACCION_FALLIDA,
    ASUNTOS,
    Aviso,
    AvisadorBrechas,
    CompuestoNotificador,
    NotificadorNulo,
    NotificadorRoto,
    SmtpNotificador,
    SmsSimuladoNotificador,
    fabricar_desde_entorno,
    notificar_mejor_esfuerzo,
)
from src.security.auditoria import EventoAuditoria

AVISO = Aviso(origen="kill-switch", asunto="Asunto de prueba", cuerpo="Cuerpo con ñ y tilde.")


# -- doble de smtplib -------------------------------------------------------

class _SMTPFalso:
    """Registro de lo que el notificador hizo con el servidor."""

    def __init__(self, host: str, puerto: int, timeout: float | None = None) -> None:
        self.host = host
        self.puerto = puerto
        self.timeout = timeout
        self.tls = False
        self.login_datos: tuple[str, str] | None = None
        self.mensajes = []
        self.cerrado = False

    def has_extn(self, nombre: str) -> bool:
        return nombre == "starttls"

    def starttls(self, context=None) -> None:
        self.tls = True

    def login(self, usuario: str, clave: str) -> None:
        self.login_datos = (usuario, clave)

    def send_message(self, mensaje) -> None:
        self.mensajes.append(mensaje)

    def __enter__(self) -> "_SMTPFalso":
        return self

    def __exit__(self, *exc) -> bool:
        self.cerrado = True
        return False


class TestSmtp:
    def _notificador(self, **kwargs) -> SmtpNotificador:
        valores = dict(
            host="correo.local", puerto=587, de="sistema@ritech.local",
            para=["operador@ritech.local"], usuario=None, clave=None,
        )
        valores.update(kwargs)
        return SmtpNotificador(**valores)

    def test_envia_con_remitente_asunto_y_cuerpo(self, monkeypatch) -> None:
        creados: list[_SMTPFalso] = []

        def _fabricar(host, puerto, timeout=None, *args, **kwargs):
            instancia = _SMTPFalso(host, puerto, timeout)
            creados.append(instancia)
            return instancia

        monkeypatch.setattr("smtplib.SMTP", _fabricar)
        self._notificador().enviar(AVISO)

        servidor = creados[0]
        assert servidor.host == "correo.local"
        assert servidor.puerto == 587
        assert servidor.timeout == 5.0
        assert len(servidor.mensajes) == 1
        mensaje = servidor.mensajes[0]
        assert mensaje["From"] == "sistema@ritech.local"
        assert mensaje["To"] == "operador@ritech.local"
        assert mensaje["Subject"] == "Asunto de prueba"
        assert "ñ y tilde" in mensaje.get_content()
        assert servidor.cerrado, "la conexión se cierra en cualquier caso"

    def test_negocia_starttls_cuando_el_servidor_lo_ofrece(self, monkeypatch) -> None:
        creados: list[_SMTPFalso] = []
        monkeypatch.setattr(
            "smtplib.SMTP",
            lambda *a, **k: creados.append(_SMTPFalso(*a, **k)) or creados[-1],
        )
        self._notificador().enviar(AVISO)
        assert creados[0].tls is True

    def test_no_se_autentica_si_no_hay_usuario(self, monkeypatch) -> None:
        creados: list[_SMTPFalso] = []
        monkeypatch.setattr(
            "smtplib.SMTP",
            lambda *a, **k: creados.append(_SMTPFalso(*a, **k)) or creados[-1],
        )
        self._notificador().enviar(AVISO)
        assert creados[0].login_datos is None

    def test_se_autentica_con_usuario_y_clave(self, monkeypatch) -> None:
        creados: list[_SMTPFalso] = []
        monkeypatch.setattr(
            "smtplib.SMTP",
            lambda *a, **k: creados.append(_SMTPFalso(*a, **k)) or creados[-1],
        )
        self._notificador(usuario="usuario", clave="secreta").enviar(AVISO)
        assert creados[0].login_datos == ("usuario", "secreta")

    def test_sin_servidor_o_sin_destinatarios_no_se_construye(self) -> None:
        with pytest.raises(ValueError, match="SMTP_HOST"):
            SmtpNotificador(host="", puerto=1, de="a@b", para=["x@y"])
        with pytest.raises(ValueError, match="SMTP_PARA"):
            SmtpNotificador(host="h", puerto=1, de="a@b", para=[])

    def test_un_fallo_de_red_se_propaga(self, monkeypatch) -> None:
        def _roto(*args, **kwargs):
            raise ConnectionRefusedError("correo caído")

        monkeypatch.setattr("smtplib.SMTP", _roto)
        with pytest.raises(ConnectionRefusedError):
            self._notificador().enviar(AVISO)


class TestSmsSimulado:
    def test_escribe_en_la_bandeja_rotulado_como_simulado(self, tmp_path) -> None:
        ruta = tmp_path / "salida.jsonl"
        SmsSimuladoNotificador(ruta).enviar(AVISO)

        lineas = ruta.read_text(encoding="utf-8").splitlines()
        assert len(lineas) == 1
        registro = json.loads(lineas[0])
        assert registro["simulado"] is True
        assert "Simulado" in registro["motivo_simulado"]
        assert "módem" in registro["motivo_simulado"] or "modem" in registro["motivo_simulado"]
        assert registro["asunto"] == "Asunto de prueba"
        assert registro["origen"] == "kill-switch"

    def test_acumula_un_registro_por_envio(self, tmp_path) -> None:
        ruta = tmp_path / "salida.jsonl"
        canal = SmsSimuladoNotificador(ruta)
        canal.enviar(AVISO)
        canal.enviar(AVISO)
        assert len(ruta.read_text(encoding="utf-8").splitlines()) == 2

    def test_crea_el_directorio_si_no_existe(self, tmp_path) -> None:
        ruta = tmp_path / "anidado" / "aun" / "mas" / "salida.jsonl"
        SmsSimuladoNotificador(ruta).enviar(AVISO)
        assert ruta.exists()


class TestCompuesto:
    def test_un_canal_fallido_no_frena_al_otro(self) -> None:
        registros: list[Aviso] = []

        class _Bueno:
            def enviar(self, aviso):
                registros.append(aviso)

        compuesto = CompuestoNotificador([NotificadorRoto("smtp caído"), _Bueno()])
        with pytest.raises(RuntimeError, match="smtp caído"):
            compuesto.enviar(AVISO)
        assert registros == [AVISO], "el segundo canal sí recibió el aviso"

    def test_si_no_falla_no_lanza(self) -> None:
        class _Bueno:
            def enviar(self, aviso):
                return None

        CompuestoNotificador([_Bueno(), _Bueno()]).enviar(AVISO)  # no lanza


class TestNulo:
    def test_no_hace_nada_ni_lanza(self) -> None:
        NotificadorNulo().enviar(AVISO)  # no lanza


class TestFabrica:
    def test_sin_configuracion_devuelve_el_nulo(self, monkeypatch) -> None:
        for variable in ("SMTP_HABILITADO", "SMS_SIMULADO_HABILITADO", "SMTP_HOST"):
            monkeypatch.delenv(variable, raising=False)
        assert isinstance(fabricar_desde_entorno(), NotificadorNulo)

    def test_smtp_configurado_da_canal_real(self, monkeypatch) -> None:
        monkeypatch.setenv("SMTP_HABILITADO", "true")
        monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
        monkeypatch.setenv("SMTP_PUERTO", "1025")
        monkeypatch.setenv("SMTP_SSL", "false")
        monkeypatch.setenv("SMTP_DE", "sistema@localhost")
        monkeypatch.setenv("SMTP_PARA", "a@localhost, b@localhost")
        monkeypatch.delenv("SMTP_USUARIO", raising=False)
        monkeypatch.delenv("SMTP_CLAVE", raising=False)
        monkeypatch.delenv("SMS_SIMULADO_HABILITADO", raising=False)

        canal = fabricar_desde_entorno()

        assert isinstance(canal, SmtpNotificador)
        assert canal.host == "127.0.0.1"
        assert canal.puerto == 1025
        assert canal.para == ["a@localhost", "b@localhost"]
        assert canal.usuario is None

    def test_smtp_habilitado_sin_host_no_explota(self, monkeypatch) -> None:
        monkeypatch.setenv("SMTP_HABILITADO", "true")
        monkeypatch.delenv("SMTP_HOST", raising=False)
        monkeypatch.delenv("SMS_SIMULADO_HABILITADO", raising=False)
        assert isinstance(fabricar_desde_entorno(), NotificadorNulo)

    def test_sms_simulado_da_la_bandeja(self, monkeypatch, tmp_path) -> None:
        monkeypatch.delenv("SMTP_HABILITADO", raising=False)
        monkeypatch.setenv("SMS_SIMULADO_HABILITADO", "1")
        monkeypatch.setenv("SMS_RUTA_SALIDA", str(tmp_path / "b.jsonl"))

        canal = fabricar_desde_entorno()

        assert isinstance(canal, SmsSimuladoNotificador)
        assert canal.ruta == tmp_path / "b.jsonl"

    def test_los_dos_canales_quedan_compuestos(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setenv("SMTP_HABILITADO", "true")
        monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
        monkeypatch.setenv("SMTP_DE", "s@localhost")
        monkeypatch.setenv("SMTP_PARA", "a@localhost")
        monkeypatch.setenv("SMS_SIMULADO_HABILITADO", "true")
        monkeypatch.setenv("SMS_RUTA_SALIDA", str(tmp_path / "b.jsonl"))

        canal = fabricar_desde_entorno()

        assert isinstance(canal, CompuestoNotificador)
        assert len(canal.canales) == 2


class _AuditorFalso:
    def __init__(self, roto: bool = False) -> None:
        self.roto = roto
        self.eventos: list[EventoAuditoria] = []

    def registrar(self, evento: EventoAuditoria) -> None:
        if self.roto:
            raise RuntimeError("postgres no responde")
        self.eventos.append(evento)


class TestMejorEsfuerzo:
    def test_el_exito_se_registra_en_auditoria(self) -> None:
        auditor = _AuditorFalso()

        class _Bueno:
            def enviar(self, aviso):
                return None

        assert notificar_mejor_esfuerzo(
            _Bueno(), auditor, aviso=AVISO, actor="SISTEMA", ip_address="127.0.0.1"
        ) is True
        assert auditor.eventos[-1].action == ACCION_ENVIADA
        assert auditor.eventos[-1].payload["enviado"] is True
        assert auditor.eventos[-1].payload["origen_aviso"] == "kill-switch"

    def test_el_fallo_se_registra_y_no_lanza(self) -> None:
        auditor = _AuditorFalso()

        resultado = notificar_mejor_esfuerzo(
            NotificadorRoto("smtp caído"), auditor, aviso=AVISO,
            actor="SISTEMA", ip_address="127.0.0.1",
            contexto={"motivo": "x"},
        )

        assert resultado is False  # nunca lanza: eso es lo best-effort
        evento = auditor.eventos[-1]
        assert evento.action == ACCION_FALLIDA
        assert evento.payload["enviado"] is False
        assert "smtp caído" in evento.payload["fallo"]
        assert evento.payload["motivo"] == "x"

    def test_si_tampoco_se_puede_registrar_va_a_la_cola(self, tmp_path) -> None:
        from src.security.auditoria_pendiente import ColaAuditoriaPendiente

        cola = ColaAuditoriaPendiente(tmp_path / "p.jsonl")
        resultado = notificar_mejor_esfuerzo(
            NotificadorRoto("x"), _AuditorFalso(roto=True), aviso=AVISO,
            actor="SISTEMA", ip_address="127.0.0.1", cola=cola,
        )

        assert resultado is False  # ni el fallo del registro lanza
        assert cola.pendientes() == 1
        assert cola.leer()[0]["evento"]["action"] == ACCION_FALLIDA

    def test_sin_notificador_no_hace_nada(self) -> None:
        auditor = _AuditorFalso()
        assert notificar_mejor_esfuerzo(
            None, auditor, aviso=AVISO, actor="SISTEMA", ip_address="127.0.0.1"
        ) is False
        assert auditor.eventos == []


class TestAvisadorDeBrechas:
    def _avisador(self, canal=None, **kwargs):
        return AvisadorBrechas(
            canal if canal is not None else NotificadorNulo(),
            _AuditorFalso(),
            ip_address="127.0.0.1",
            **kwargs,
        )

    def test_avisa_una_vez_por_sesion(self) -> None:
        avisador = self._avisador()
        hoy = date(2026, 11, 5)

        # El nulo «envía» sin hacer nada, así que el primero sale True.
        assert avisador.avisar(fecha=hoy, motivo="sin-cuota", detalle="x") is True
        assert avisador.avisos_enviados == [hoy]
        # El segundo no debe ni intentarse: la marca ya está puesta.
        assert avisador.avisar(fecha=hoy, motivo="sin-cuota", detalle="y") is False
        assert avisador.avisos_enviados == [hoy]

    def test_al_dia_siguiente_vuelve_a_avisar(self) -> None:
        avisador = self._avisador()
        avisador.avisar(fecha=date(2026, 11, 5), motivo="m", detalle="d")
        assert len(avisador.avisos_enviados) == 1

        avisador.avisar(fecha=date(2026, 11, 6), motivo="m", detalle="d")
        assert len(avisador.avisos_enviados) == 2

    def test_sin_canal_no_hace_nada(self) -> None:
        avisador = AvisadorBrechas(None, _AuditorFalso())
        assert avisador.avisar(fecha=date(2026, 11, 5), motivo="m", detalle="d") is False
        assert avisador.avisos_enviados == []

    def test_un_envio_fallido_no_se_reintenta_en_bucle(self) -> None:
        # La marca se pone antes de enviar: un SMTP caído no puede generar
        # un intento por tick (cada 5 minutos) durante toda la sesión.
        canal = NotificadorRoto("smtp caído")
        avisador = self._avisador(canal)
        hoy = date(2026, 11, 5)

        assert avisador.avisar(fecha=hoy, motivo="sin-cuota", detalle="d") is False
        assert len(canal.intentos) == 1, "solo un intento"
        avisador.avisar(fecha=hoy, motivo="sin-cuota", detalle="d")
        assert len(canal.intentos) == 1, "y no se reintenta hoy"

    def test_el_asunto_de_la_brecha_es_el_de_la_lista(self) -> None:
        assert "brecha" in ASUNTOS["brecha-datos"].lower()
