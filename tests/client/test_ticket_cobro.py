"""Ticket de cobro de 80 mm para la impresora térmica de la caja.

Pura función sobre `documents.py`, sin Qt ni gRPC ni DB, igual que el resto de
`tests/client/` -- por eso corre en una PC cliente con
`pytest tests/client --noconftest`.

Lo que estos tests protegen es el *contenido exigido*: la entidad, el cliente,
el pago, el cajero, las cuotas abonadas y el espacio de sello y firma. Son los
seis datos que se pidieron para el papel que se entrega en ventanilla, y
ninguno tiene otra fuente: si uno desaparece del template, nadie lo nota hasta
que un cliente reclama un cobro y el ticket no dice quién lo recibió.
"""

from datetime import datetime, timezone

import pytest

from cas_client import documents


class _FakeTimestamp:
    """Lo mínimo de google.protobuf.Timestamp que usa el template: devolver un
    datetime **naive en UTC**, que es exactamente lo que da ToDatetime() y el
    motivo de que el ticket tenga que convertir a hora local."""

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


def _ticket(**kwargs) -> str:
    return documents.ticket_cobro_html(
        _FakeLoan(), _FakeClient(), _FakePayment(**kwargs)
    )


def test_el_ticket_identifica_a_la_entidad():
    html = _ticket()
    assert documents._COMPANY_NAME in html
    assert documents._COMPANY_RUC in html
    assert documents._COMPANY_ADDRESS in html
    assert documents._COMPANY_PHONE in html


def test_el_ticket_identifica_al_cliente():
    html = _ticket()
    assert "María Beatriz" in html and "González Ayala" in html
    assert "4.582.113" in html
    assert "(0985) 447 210" in html


def test_el_ticket_nombra_al_cajero_que_cobro():
    """Es el dato que el comprobante A4 trae como "Registrado por" y que en el
    ticket es la única constancia de quién recibió la plata en ventanilla."""
    html = _ticket()
    assert "Lucía Tavarozzi" in html
    assert "3.211.998" in html
    assert "Cajero/a" in html


def test_el_ticket_dice_que_cuotas_se_abonaron():
    html = _ticket()
    assert "Cuota(s) 3,4 de 12" in html


def test_el_ticket_deja_espacio_de_sello_y_firma():
    html = _ticket()
    assert "SELLO Y FIRMA" in html


def test_el_monto_y_el_saldo_salen_formateados_en_guaranies():
    html = _ticket()
    assert "500.000 Gs" in html
    assert "1.750.000 Gs" in html


def test_la_hora_del_ticket_es_local_y_no_utc():
    """paid_at viaja como naive en UTC. Un ticket que el cliente se lleva
    impreso tiene que decir la hora a la que pagó, no su equivalente en UTC --
    el mismo error que se arregló una vez en el comprobante A4."""
    html = _ticket(paid_at=_FakeTimestamp(datetime(2026, 9, 14, 12, 30, 0)))
    esperado = documents.fecha_hora(datetime(2026, 9, 14, 12, 30, 0))
    assert esperado in html
    # 12:30 UTC no es 12:30 en Paraguay (UTC-3/-4); si el template hiciera un
    # strftime propio, imprimiría la hora de UTC.
    local = documents.a_hora_local(datetime(2026, 9, 14, 12, 30, 0))
    assert local.hour != 12 or local.utcoffset() == timezone.utc.utcoffset(None)


def test_en_efectivo_no_imprime_una_referencia_vacia():
    """Misma regla que el comprobante A4 (filas_medio_de_pago): un renglón
    "Referencia:" en blanco parece un dato que se perdió."""
    html = _ticket(payment_method="EFECTIVO", transfer_reference="")
    assert "Referencia" not in html


def test_en_transferencia_imprime_la_referencia():
    html = _ticket(payment_method="TRANSFERENCIA", transfer_reference="TRF-889321")
    assert "TRF-889321" in html


def test_avisa_cuando_el_prestamo_queda_cancelado():
    html = _ticket(status="PAID", remaining_balance="0.00")
    assert "TOTALMENTE CANCELADO" in html
    assert "TOTALMENTE CANCELADO" not in _ticket(status="ACTIVE")


def test_sin_datos_del_operador_no_deja_el_campo_en_blanco():
    """Los usuarios anteriores a BR-AUTH-006 no tienen nombre ni C.I. El
    ticket cae a "No registrado" (responsable()) en vez de entregar un papel
    con el renglón del cajero vacío."""
    html = _ticket(recorded_by_name="", recorded_by_national_id="")
    assert "No registrado" in html


def test_el_numero_de_ticket_identifica_al_cobro():
    """No hay id de pago en el contrato, así que el número se deriva del
    préstamo y del instante del cobro. Dos cobros distintos no pueden
    compartirlo."""
    primero = documents.numero_ticket(
        _FakeLoan(), _FakePayment(paid_at=_FakeTimestamp(datetime(2026, 9, 14, 12, 30)))
    )
    segundo = documents.numero_ticket(
        _FakeLoan(), _FakePayment(paid_at=_FakeTimestamp(datetime(2026, 9, 14, 12, 31)))
    )
    assert primero != segundo
    assert primero.startswith("A1B2C3D4-")
    assert documents.numero_ticket(_FakeLoan(), _FakePayment()) in _ticket()


def test_el_cierre_agradece_y_no_habla_de_variaciones_de_costos():
    """Pedido explícito de la entidad: el pie del ticket no menciona que los
    montos puedan variar.

    El ticket es lo que el cliente se lleva de ventanilla, y una advertencia
    sobre costos o precios que pueden cambiar se lee como una reserva sobre el
    cobro que acaba de hacer -- justo al pie de un papel cuyo sentido es dejar
    constancia de que pagó. La salvedad sobre cargos y ajustes posteriores
    sigue estando donde corresponde: el Comprobante de Pago A4.
    """
    html = _ticket()
    assert "Gracias por su pago" in html
    assert "constancia de la operaci" in html
    for prohibido in ("variar", "varía", "ajustes posteriores", "cargos o"):
        assert prohibido not in html, prohibido


def test_el_ticket_no_lleva_el_logo():
    """Deliberado: un bitmap a color en una térmica sale tramado y lento, y
    varios drivers de estas impresoras directamente no lo imprimen. Si alguien
    vuelve a meter el header A4 acá, este test lo frena."""
    html = _ticket()
    assert "<img" not in html
    assert "data:image" not in html


def test_el_ticket_no_usa_color():
    """Una térmica es monocromática: cualquier color sale como un tramado gris
    que se lee peor que el negro pleno."""
    html = _ticket()
    for color in ("#2B407B", "#8CC63F", "#2E9344"):
        assert color not in html


@pytest.mark.parametrize(
    "campo",
    ["amount_paid", "total_paid", "remaining_balance"],
)
def test_los_montos_del_ticket_salen_del_servidor(campo):
    """El ticket describe lo que quedó registrado, no lo que el cliente creía
    haber enviado: cada monto viene de RecordPaymentResponse."""
    html = _ticket(**{campo: "987654.00"})
    assert "987.654 Gs" in html
