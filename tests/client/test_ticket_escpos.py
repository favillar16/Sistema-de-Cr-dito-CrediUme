"""Ticket de cobro de 80 mm en ESC/POS crudo (cas_client/escpos.py).

Pura función, sin win32print ni Qt, igual que `documents.py` -- corre en
cualquier PC con `pytest tests/client --noconftest`, no solo en la caja real.

Mismo criterio de cobertura que `test_ticket_cobro.py` (la versión HTML,
todavía la plantilla de referencia -- ver el docstring de escpos.py): lo que
se protege acá es el *contenido exigido*, ahora decodificado de los bytes
CP437 en vez de buscado en HTML. Los dos tienen que seguir de acuerdo porque
describen el mismo cobro.
"""

import re
from datetime import datetime, timezone

import pytest

from cas_client import escpos

# Tira los comandos ESC/GS del módulo antes de medir el ancho visible de cada
# línea -- sin esto, los bytes de control (que no ocupan lugar en el papel)
# cuentan como si fueran caracteres impresos y el chequeo de columnas da
# falsos positivos.
_COMANDO = re.compile(rb"\x1b@|\x1b[tadE].|\x1d[!V].")


class _FakeTimestamp:
    def __init__(self, momento: datetime) -> None:
        self._momento = momento

    def ToDatetime(self) -> datetime:
        return self._momento


class _FakeClient:
    first_name = "María Beatriz"
    last_name = "González Ayala"
    national_id = "4.582.113"
    phone_number = "(0985) 447 210"
    address = "Barrio San Blas, Coronel Oviedo"


class _FakeLoan:
    id = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"


class _FakePayment:
    def __init__(self, **kwargs) -> None:
        self.status = "ACTIVE"
        self.amount_paid = "500000.00"
        self.total_paid = "1500000.00"
        self.remaining_balance = "1750000.00"
        self.covered_installments = [3, 4]
        self.total_installments = 12
        self.payment_method = "EFECTIVO"
        self.transfer_reference = ""
        self.recorded_by_name = "Lucía Tavarozzi"
        self.recorded_by_national_id = "3.211.998"
        self.paid_at = _FakeTimestamp(datetime(2026, 9, 14, 12, 30, 0))
        self.__dict__.update(kwargs)


def _texto(**kwargs) -> str:
    """Decodifica el ticket como lo leería un humano, para poder usar `in`
    sobre el resultado igual que con el HTML."""
    datos = escpos.ticket_cobro_escpos(
        _FakeLoan(), _FakeClient(), _FakePayment(**kwargs)
    )
    return datos.decode("cp437")


def test_el_ticket_identifica_a_la_entidad():
    texto = _texto()
    from cas_client.documents import _COMPANY_NAME, _COMPANY_PHONE, _COMPANY_RUC

    assert _COMPANY_NAME in texto
    assert _COMPANY_RUC in texto
    # No _COMPANY_ADDRESS tal cual: CP437 no tiene raya (—), así que
    # escpos._sanear() la baja a un guion común antes de imprimir (ver su
    # docstring) -- separar la dirección en sus dos mitades no depende de
    # qué separador ASCII se haya elegido.
    assert "Ayolas c/ Acaray" in texto
    assert "Coronel Oviedo, Paraguay" in texto
    assert _COMPANY_PHONE in texto


def test_el_ticket_identifica_al_cliente():
    texto = _texto()
    assert "María Beatriz" in texto and "González Ayala" in texto
    assert "4.582.113" in texto
    assert "(0985) 447 210" in texto


def test_el_ticket_nombra_al_cajero_que_cobro():
    texto = _texto()
    assert "Lucía Tavarozzi" in texto
    assert "3.211.998" in texto
    assert "Cajero/a" in texto


def test_el_ticket_dice_que_cuotas_se_abonaron():
    texto = _texto()
    assert "Cuota(s) 3,4 de 12" in texto


def test_el_ticket_deja_espacio_de_sello_y_firma():
    texto = _texto()
    assert "SELLO Y FIRMA" in texto


def test_el_monto_y_el_saldo_salen_formateados_en_guaranies():
    texto = _texto()
    assert "500.000 Gs" in texto
    assert "1.750.000 Gs" in texto


def test_la_hora_del_ticket_es_local_y_no_utc():
    from cas_client import documents

    texto = _texto(paid_at=_FakeTimestamp(datetime(2026, 9, 14, 12, 30, 0)))
    esperado = documents.fecha_hora(datetime(2026, 9, 14, 12, 30, 0))
    assert esperado in texto
    local = documents.a_hora_local(datetime(2026, 9, 14, 12, 30, 0))
    assert local.hour != 12 or local.utcoffset() == timezone.utc.utcoffset(None)


def test_en_efectivo_no_imprime_una_referencia_vacia():
    texto = _texto(payment_method="EFECTIVO", transfer_reference="")
    assert "Referencia" not in texto


def test_en_transferencia_imprime_la_referencia():
    texto = _texto(payment_method="TRANSFERENCIA", transfer_reference="TRF-889321")
    assert "TRF-889321" in texto


def test_avisa_cuando_el_prestamo_queda_cancelado():
    assert "TOTALMENTE CANCELADO" in _texto(status="PAID", remaining_balance="0.00")
    assert "TOTALMENTE CANCELADO" not in _texto(status="ACTIVE")


def test_sin_datos_del_operador_no_deja_el_campo_en_blanco():
    texto = _texto(recorded_by_name="", recorded_by_national_id="")
    assert "No registrado" in texto


def test_el_numero_de_ticket_identifica_al_cobro():
    from cas_client import documents

    primero = documents.numero_ticket(
        _FakeLoan(), _FakePayment(paid_at=_FakeTimestamp(datetime(2026, 9, 14, 12, 30)))
    )
    assert primero.startswith("A1B2C3D4-")
    assert primero in _texto()


def test_el_cierre_agradece_y_no_habla_de_variaciones_de_costos():
    texto = _texto()
    assert "Gracias por su pago" in texto
    # En dos líneas separadas (a diferencia del HTML, un solo párrafo): a
    # 48 columnas monoespaciadas la frase completa no entra en una.
    assert "constancia" in texto
    assert "de la operaci" in texto
    for prohibido in ("variar", "varía", "ajustes posteriores", "cargos o"):
        assert prohibido not in texto, prohibido


@pytest.mark.parametrize("campo", ["amount_paid", "total_paid", "remaining_balance"])
def test_los_montos_del_ticket_salen_del_servidor(campo):
    texto = _texto(**{campo: "987654.00"})
    assert "987.654 Gs" in texto


def test_el_ticket_marca_reimpresion():
    """No cubierto por test_reimpresion.py (que solo prueba la versión HTML)
    -- BR-LOAN-016 exige que el papel se declare a sí mismo un duplicado sea
    cual sea el formato en que sale."""
    texto = _texto(**{"es_reimpresion": True})
    assert "REIMPRESI" in texto


def test_las_lineas_no_exceden_el_ancho_de_columnas():
    """Font A es monoespaciada: una línea más larga que _COLUMNAS se corta o
    se pisa en el papel real en vez de dar la vuelta con buen aspecto como en
    el ticket HTML/QTextDocument (fuente proporcional)."""
    datos = escpos.ticket_cobro_escpos(_FakeLoan(), _FakeClient(), _FakePayment())
    visible = _COMANDO.sub(b"", datos)
    for linea in visible.decode("cp437").split("\n"):
        assert len(linea) <= escpos._COLUMNAS, linea


def test_los_comandos_esc_pos_estan_presentes():
    """Que el resultado sean bytes ESC/POS de verdad y no solo texto plano:
    inicializa, fija la codificación y corta el papel al final."""
    datos = escpos.ticket_cobro_escpos(_FakeLoan(), _FakeClient(), _FakePayment())
    assert datos.startswith(escpos._INICIALIZAR)
    assert escpos._CORTE_PARCIAL in datos
