"""BR-LOAN-018: cancelación del préstamo en un solo pago con descuento.

Archivo propio, mismo criterio que test_mora.py: la regla no agregó ninguna RPC
(es un modo de RecordPayment, mismo tier), así que lo que hay que fijar es la
aritmética y sus límites, no quién puede llamarla. El caso en efectivo, que
necesita credenciales y caja abierta, vive en test_cash_interceptor_integration.py.

Los tres límites son decisiones de la entidad (2026-09-30), no detalles:

1.  **El descuento lo decide el operador**, en guaraníes, sin porcentaje.
2.  **La mora no se descuenta**: se cobra completa, como en un cobro por cuota.
3.  **Tiene que quedar algo del saldo por cobrar**: descuento < saldo.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import grpc
import loan_service_pb2
import pytest

from cas_server.db.base import SessionLocal
from cas_server.db.models import AuditLog, LoanPayment

from tests.server.helpers import AbortCalled, FakeContext
from cas_server import config
from cas_server.services.loan_service import LoanServicer
from tests.server.test_mora import _CAPITAL, _CUOTA, _prestamo_activo_vencido


@pytest.fixture
def servicer():
    return LoanServicer()


@pytest.fixture
def mora_ya_vigente(monkeypatch):
    """Mismo recurso que en test_mora.py: la fecha de activación bien atrás."""
    monkeypatch.setattr(config, "LOAN_LATE_FEE_START_DATE", date(2000, 1, 1))


def _pagar_todo(servicer, loan_id, descuento="", **extra):
    return servicer.RecordPayment(
        loan_service_pb2.RecordPaymentRequest(
            loan_id=loan_id,
            transfer_reference="TRF-TOTAL",
            pay_in_full=True,
            discount_amount=descuento,
            **extra,
        ),
        FakeContext(),
    )


def test_el_pago_total_cancela_el_prestamo(servicer):
    loan_id = _prestamo_activo_vencido(servicer, dias_de_atraso=0)

    respuesta = _pagar_todo(servicer, loan_id)

    assert respuesta.status == "PAID"
    assert respuesta.paid_in_full is True
    assert Decimal(respuesta.amount_paid) == _CAPITAL
    assert Decimal(respuesta.remaining_balance) == Decimal("0")
    assert list(respuesta.covered_installments) == list(range(1, 13))
    assert Decimal(respuesta.discount_amount) == Decimal("0")


def test_el_descuento_baja_lo_cobrado_pero_imputa_el_saldo_entero(servicer):
    """`amount` es el saldo entero -- es lo que deja el préstamo en PAID por el
    camino de siempre --; lo que entregó el cliente es el neto."""
    loan_id = _prestamo_activo_vencido(servicer, dias_de_atraso=0)

    respuesta = _pagar_todo(servicer, loan_id, "150000")

    assert respuesta.status == "PAID"
    assert Decimal(respuesta.amount_paid) == _CAPITAL
    assert Decimal(respuesta.discount_amount) == Decimal("150000.00")
    assert Decimal(respuesta.total_charged) == _CAPITAL - Decimal("150000")
    with SessionLocal() as session:
        pago = session.query(LoanPayment).one()
        assert pago.amount == _CAPITAL
        assert pago.discount_amount == Decimal("150000.00")
        auditoria = [fila.action for fila in session.query(AuditLog).all()]
    assert any("pago_total=1 descuento=150000.00" in a for a in auditoria)


def test_el_saldo_lo_calcula_el_servidor_y_no_el_cliente(servicer):
    """Mismo criterio que BR-LOAN-010: `amount` e `installment_number` se
    ignoran en una cancelación."""
    loan_id = _prestamo_activo_vencido(servicer, dias_de_atraso=0)

    respuesta = _pagar_todo(servicer, loan_id, amount="1", installment_number=3)

    assert Decimal(respuesta.amount_paid) == _CAPITAL


def test_el_pago_total_despues_de_una_cuota_cobra_solo_el_resto(servicer):
    loan_id = _prestamo_activo_vencido(servicer, dias_de_atraso=0)
    servicer.RecordPayment(
        loan_service_pb2.RecordPaymentRequest(
            loan_id=loan_id, transfer_reference="TRF-1", installment_number=1
        ),
        FakeContext(),
    )

    respuesta = _pagar_todo(servicer, loan_id)

    assert Decimal(respuesta.amount_paid) == _CAPITAL - _CUOTA
    assert list(respuesta.covered_installments) == list(range(2, 13))
    assert respuesta.status == "PAID"


def test_la_mora_se_cobra_completa_y_no_entra_en_el_descuento(
    servicer, mora_ya_vigente
):
    loan_id = _prestamo_activo_vencido(servicer, dias_de_atraso=35)
    mora_devengada = Decimal(
        servicer.GetLoanById(
            loan_service_pb2.GetLoanByIdRequest(loan_id=loan_id), FakeContext()
        ).accrued_late_fee
    )
    assert mora_devengada > Decimal("0")

    respuesta = _pagar_todo(servicer, loan_id, "100000")

    assert Decimal(respuesta.late_fee_amount) == mora_devengada
    assert Decimal(respuesta.total_charged) == (
        _CAPITAL - Decimal("100000") + mora_devengada
    )


@pytest.mark.parametrize("descuento", ["1200000", "1200000.00", "1500000"])
def test_el_descuento_no_puede_alcanzar_el_saldo(servicer, descuento):
    loan_id = _prestamo_activo_vencido(servicer, dias_de_atraso=0)

    with pytest.raises(AbortCalled) as exc:
        _pagar_todo(servicer, loan_id, descuento)

    assert exc.value.code == grpc.StatusCode.INVALID_ARGUMENT
    with SessionLocal() as session:
        assert session.query(LoanPayment).count() == 0


def test_el_descuento_maximo_admitido_deja_un_guarani_por_cobrar(servicer):
    loan_id = _prestamo_activo_vencido(servicer, dias_de_atraso=0)

    respuesta = _pagar_todo(servicer, loan_id, str(_CAPITAL - Decimal("1")))

    assert respuesta.status == "PAID"
    assert Decimal(respuesta.total_charged) == Decimal("1")


def test_un_descuento_negativo_se_rechaza(servicer):
    loan_id = _prestamo_activo_vencido(servicer, dias_de_atraso=0)

    with pytest.raises(AbortCalled) as exc:
        _pagar_todo(servicer, loan_id, "-10")

    assert exc.value.code == grpc.StatusCode.INVALID_ARGUMENT


def test_un_descuento_sin_pago_total_se_rechaza(servicer):
    """Un cobro por cuota no admite descuento: la regla es de cancelación."""
    loan_id = _prestamo_activo_vencido(servicer, dias_de_atraso=0)

    with pytest.raises(AbortCalled) as exc:
        servicer.RecordPayment(
            loan_service_pb2.RecordPaymentRequest(
                loan_id=loan_id,
                transfer_reference="TRF-1",
                installment_number=1,
                discount_amount="1000",
            ),
            FakeContext(),
        )

    assert exc.value.code == grpc.StatusCode.INVALID_ARGUMENT


def test_un_prestamo_ya_cancelado_no_admite_otro_pago_total(servicer):
    loan_id = _prestamo_activo_vencido(servicer, dias_de_atraso=0)
    _pagar_todo(servicer, loan_id)

    with pytest.raises(AbortCalled) as exc:
        _pagar_todo(servicer, loan_id)

    assert exc.value.code == grpc.StatusCode.FAILED_PRECONDITION


def test_el_historial_devuelve_el_descuento_guardado(servicer):
    """BR-LOAN-016: el comprobante reimpreso tiene que decir lo mismo que el
    original, descuento incluido."""
    loan_id = _prestamo_activo_vencido(servicer, dias_de_atraso=0)
    cobro = _pagar_todo(servicer, loan_id, "200000")

    entrada = servicer.ListLoanPayments(
        loan_service_pb2.ListLoanPaymentsRequest(loan_id=loan_id), FakeContext()
    ).payments[0]

    assert Decimal(entrada.discount_amount) == Decimal("200000.00")
    assert Decimal(entrada.total_charged) == Decimal(cobro.total_charged)
    assert Decimal(entrada.remaining_balance_after) == Decimal("0")


def test_la_ficha_del_prestamo_informa_el_descuento_concedido(servicer):
    """`total_paid` incluye lo condonado (es lo imputado al cronograma); la
    ficha necesita el descuento aparte para no decir que se cobró todo."""
    loan_id = _prestamo_activo_vencido(servicer, dias_de_atraso=0)
    antes = servicer.GetLoanById(
        loan_service_pb2.GetLoanByIdRequest(loan_id=loan_id), FakeContext()
    )
    assert Decimal(antes.total_discount) == Decimal("0")

    _pagar_todo(servicer, loan_id, "200000")

    detalle = servicer.GetLoanById(
        loan_service_pb2.GetLoanByIdRequest(loan_id=loan_id), FakeContext()
    )
    assert detalle.status == "PAID"
    assert Decimal(detalle.total_paid) == _CAPITAL
    assert Decimal(detalle.total_discount) == Decimal("200000.00")


def test_un_cobro_por_cuota_informa_descuento_vacio(servicer):
    loan_id = _prestamo_activo_vencido(servicer, dias_de_atraso=0)
    servicer.RecordPayment(
        loan_service_pb2.RecordPaymentRequest(
            loan_id=loan_id, transfer_reference="TRF-1", installment_number=1
        ),
        FakeContext(),
    )

    entrada = servicer.ListLoanPayments(
        loan_service_pb2.ListLoanPaymentsRequest(loan_id=loan_id), FakeContext()
    ).payments[0]

    assert entrada.discount_amount == ""


def test_la_caja_recibe_el_saldo_menos_el_descuento_mas_la_mora():
    """Mismo recurso que test_mora.py: el helper de cash_service directo."""
    from cas_server.services.cash_service import registrar_cobro_en_efectivo

    pago = LoanPayment(
        loan_id=uuid.uuid4(),
        amount=Decimal("1000000.00"),
        discount_amount=Decimal("100000.00"),
        late_fee_amount=Decimal("1250.00"),
        paid_at=datetime.now(timezone.utc),
    )
    sesion_caja = type("SesionFalsa", (), {"id": uuid.uuid4()})()
    sesion_falsa = type("Sesion", (), {"add": lambda self, x: None})()

    movimiento = registrar_cobro_en_efectivo(
        sesion_falsa, sesion_caja, pago, None, datetime.now(timezone.utc)
    )

    assert movimiento.amount == Decimal("901250.00")
