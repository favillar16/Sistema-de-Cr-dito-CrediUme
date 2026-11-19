"""BR-LOAN-019: eliminación de un cobro registrado por error.

Archivo propio por el mismo motivo que test_loan_deletion.py: lo interesante
no es "quién puede llamarla" (cualquier rol, igual que RecordPayment) sino qué
arrastra el borrado -- el movimiento de caja si la caja sigue abierta, el
rechazo si ya cerró, el regreso de PAID a ACTIVE y la reimputación FIFO de los
cobros posteriores.

Servidor real con AuthInterceptor y los tres servicers: el cobro en efectivo
necesita un login de verdad y una caja abierta (ver
test_cash_interceptor_integration.py).
"""

from concurrent import futures
from datetime import date, datetime, timezone
from decimal import Decimal

import auth_service_pb2
import auth_service_pb2_grpc
import cash_service_pb2
import cash_service_pb2_grpc
import grpc
import loan_service_pb2
import loan_service_pb2_grpc
import pytest

from cas_server.db.base import SessionLocal
from cas_server.db.models import (
    AuditLog,
    CashMovement,
    Client,
    Loan,
    LoanPayment,
    LoanStatusEnum,
    RoleEnum,
    User,
)
from cas_server.security.interceptor import AuthInterceptor
from cas_server.security.passwords import hash_password
from cas_server.services.auth_service import AuthServicer
from cas_server.services.cash_service import CashServicer
from cas_server.services.loan_service import LoanServicer


@pytest.fixture
def stubs():
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=4), interceptors=[AuthInterceptor()]
    )
    auth_service_pb2_grpc.add_AuthServiceServicer_to_server(AuthServicer(), server)
    cash_service_pb2_grpc.add_CashServiceServicer_to_server(CashServicer(), server)
    loan_service_pb2_grpc.add_LoanServiceServicer_to_server(LoanServicer(), server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()

    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        yield (
            auth_service_pb2_grpc.AuthServiceStub(channel),
            cash_service_pb2_grpc.CashServiceStub(channel),
            loan_service_pb2_grpc.LoanServiceStub(channel),
        )
    finally:
        channel.close()
        server.stop(grace=None)


def _usuario(auth_stub, username: str, role: RoleEnum = RoleEnum.CASHIER):
    with SessionLocal() as session:
        session.add(
            User(username=username, password_hash=hash_password("Passw0rd!"), role=role)
        )
        session.commit()
    respuesta = auth_stub.Login(
        auth_service_pb2.LoginRequest(username=username, password="Passw0rd!")
    )
    return (("authorization", f"Bearer {respuesta.access_token}"),)


def _prestamo_activo(national_id: str = "9100001", email: str = "cobro@example.com"):
    ahora = datetime.now(timezone.utc)
    with SessionLocal() as session:
        cliente = Client(
            first_name="Cobro",
            last_name="Erroneo",
            national_id=national_id,
            email=email,
            phone_number="0981555555",
            date_of_birth=date(1990, 1, 1),
            address="Calle Cobro 1",
            declared_monthly_income=Decimal("5000.00"),
            is_active=True,
            created_at=ahora,
            updated_at=ahora,
        )
        session.add(cliente)
        session.flush()
        prestamo = Loan(
            client_id=cliente.id,
            principal_amount=Decimal("1200.00"),
            interest_rate=Decimal("0.20"),
            term_months=12,
            # Hoy: ninguna cuota está vencida, así que no hay mora que
            # complique las cifras.
            first_due_date=ahora.date(),
            status=LoanStatusEnum.ACTIVE,
            created_at=ahora,
            approved_at=ahora,
        )
        session.add(prestamo)
        session.commit()
        return prestamo.id


def _cobrar(loan_stub, metadata, loan_id, cuota=1, medio="TRANSFERENCIA", **extra):
    return loan_stub.RecordPayment(
        loan_service_pb2.RecordPaymentRequest(
            loan_id=str(loan_id),
            installment_number=cuota,
            payment_method=medio,
            transfer_reference="TRX-1" if medio == "TRANSFERENCIA" else "",
            **extra,
        ),
        metadata=metadata,
    )


def _eliminar(loan_stub, metadata, payment_id, motivo="Cargado por error"):
    return loan_stub.DeleteLoanPayment(
        loan_service_pb2.DeleteLoanPaymentRequest(payment_id=payment_id, reason=motivo),
        metadata=metadata,
    )


def _abrir_caja(cash_stub, metadata, monto="100000.00"):
    cash_stub.OpenCashSession(
        cash_service_pb2.OpenCashSessionRequest(opening_amount=monto), metadata=metadata
    )


def _caja_actual(cash_stub, metadata):
    return cash_stub.GetCurrentCashSession(
        cash_service_pb2.GetCurrentCashSessionRequest(), metadata=metadata
    ).session


def _pago_existe(payment_id: str) -> bool:
    with SessionLocal() as session:
        return session.get(LoanPayment, payment_id) is not None


def test_cashier_deletes_a_transfer_payment_and_the_balance_goes_back(stubs):
    auth_stub, _, loan_stub = stubs
    metadata = _usuario(auth_stub, "cajero1")
    loan_id = _prestamo_activo()
    pago = _cobrar(loan_stub, metadata, loan_id)

    respuesta = _eliminar(loan_stub, metadata, pago.payment_id)

    assert respuesta.success is True
    assert respuesta.loan_id == str(loan_id)
    assert respuesta.status == "ACTIVE"
    assert Decimal(respuesta.total_paid) == Decimal("0")
    assert Decimal(respuesta.remaining_balance) == Decimal(pago.remaining_balance) + (
        Decimal(pago.amount_paid)
    )
    assert respuesta.cash_amount_removed == ""
    assert not _pago_existe(pago.payment_id)
    with SessionLocal() as session:
        acciones = [fila.action for fila in session.query(AuditLog).all()]
    assert any(
        a.startswith("COBRO_ELIMINADO") and "motivo=Cargado por error" in a
        for a in acciones
    )


def test_reason_is_required_and_nothing_is_deleted_without_it(stubs):
    auth_stub, _, loan_stub = stubs
    metadata = _usuario(auth_stub, "cajero2")
    pago = _cobrar(loan_stub, metadata, _prestamo_activo())

    with pytest.raises(grpc.RpcError) as exc:
        _eliminar(loan_stub, metadata, pago.payment_id, motivo="   ")

    assert exc.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert _pago_existe(pago.payment_id)


def test_unknown_payment_is_not_found(stubs):
    auth_stub, _, loan_stub = stubs
    metadata = _usuario(auth_stub, "cajero3")

    with pytest.raises(grpc.RpcError) as exc:
        _eliminar(loan_stub, metadata, "00000000-0000-0000-0000-000000000000")

    assert exc.value.code() == grpc.StatusCode.NOT_FOUND


def test_requires_a_token(stubs):
    _, _, loan_stub = stubs

    with pytest.raises(grpc.RpcError) as exc:
        loan_stub.DeleteLoanPayment(
            loan_service_pb2.DeleteLoanPaymentRequest(
                payment_id="00000000-0000-0000-0000-000000000000", reason="x"
            )
        )

    assert exc.value.code() == grpc.StatusCode.UNAUTHENTICATED


def test_cash_payment_with_the_till_still_open_is_removed_from_the_till(stubs):
    auth_stub, cash_stub, loan_stub = stubs
    metadata = _usuario(auth_stub, "cajero4")
    _abrir_caja(cash_stub, metadata)
    pago = _cobrar(loan_stub, metadata, _prestamo_activo(), medio="EFECTIVO")
    assert len(_caja_actual(cash_stub, metadata).movements) == 1

    respuesta = _eliminar(loan_stub, metadata, pago.payment_id)

    caja = _caja_actual(cash_stub, metadata)
    assert Decimal(respuesta.cash_amount_removed) == Decimal(pago.total_charged)
    assert len(caja.movements) == 0
    # El arqueo vuelve a esperar sólo el fondo inicial: la plata nunca entró.
    assert Decimal(caja.expected_amount) == Decimal("100000.00")
    with SessionLocal() as session:
        assert session.query(CashMovement).count() == 0


def test_another_operator_can_delete_it_while_the_till_is_open(stubs):
    """Cualquier rol puede eliminar cualquier cobro: el movimiento se quita de
    la caja de quien cobró, no de la de quien corrige."""
    auth_stub, cash_stub, loan_stub = stubs
    cajero = _usuario(auth_stub, "cajero5")
    gerente = _usuario(auth_stub, "gerente5", RoleEnum.MANAGER)
    _abrir_caja(cash_stub, cajero)
    pago = _cobrar(loan_stub, cajero, _prestamo_activo(), medio="EFECTIVO")

    _eliminar(loan_stub, gerente, pago.payment_id)

    assert len(_caja_actual(cash_stub, cajero).movements) == 0


def test_cash_payment_in_a_closed_till_cannot_be_deleted(stubs):
    auth_stub, cash_stub, loan_stub = stubs
    metadata = _usuario(auth_stub, "cajero6")
    _abrir_caja(cash_stub, metadata)
    pago = _cobrar(loan_stub, metadata, _prestamo_activo(), medio="EFECTIVO")
    esperado = _caja_actual(cash_stub, metadata).expected_amount
    cash_stub.CloseCashSession(
        cash_service_pb2.CloseCashSessionRequest(counted_amount=esperado),
        metadata=metadata,
    )

    with pytest.raises(grpc.RpcError) as exc:
        _eliminar(loan_stub, metadata, pago.payment_id)

    assert exc.value.code() == grpc.StatusCode.FAILED_PRECONDITION
    assert _pago_existe(pago.payment_id)
    with SessionLocal() as session:
        assert session.query(CashMovement).count() == 1


def test_deleting_the_payoff_puts_a_paid_loan_back_to_active(stubs):
    auth_stub, _, loan_stub = stubs
    metadata = _usuario(auth_stub, "cajero7")
    loan_id = _prestamo_activo()
    pago = loan_stub.RecordPayment(
        loan_service_pb2.RecordPaymentRequest(
            loan_id=str(loan_id), pay_in_full=True, transfer_reference="TRX-TOTAL"
        ),
        metadata=metadata,
    )
    assert pago.status == "PAID"

    respuesta = _eliminar(loan_stub, metadata, pago.payment_id)

    assert respuesta.status == "ACTIVE"
    with SessionLocal() as session:
        assert session.get(Loan, loan_id).status == LoanStatusEnum.ACTIVE
        acciones = [fila.action for fila in session.query(AuditLog).all()]
    assert any(a.startswith("PRESTAMO_REACTIVADO") for a in acciones)


def test_deleting_an_older_payment_reimputes_the_later_ones(stubs):
    """Consecuencia asumida de permitir borrar cualquier cobro (no sólo el
    último): la imputación es FIFO y se reconstruye, así que el cobro que
    pagó la cuota 2 pasa a figurar como pago de la cuota 1."""
    auth_stub, _, loan_stub = stubs
    metadata = _usuario(auth_stub, "cajero8")
    loan_id = _prestamo_activo()
    primero = _cobrar(loan_stub, metadata, loan_id, cuota=1)
    segundo = _cobrar(loan_stub, metadata, loan_id, cuota=2)
    assert list(segundo.covered_installments) == [2]

    _eliminar(loan_stub, metadata, primero.payment_id)

    historial = loan_stub.ListLoanPayments(
        loan_service_pb2.ListLoanPaymentsRequest(loan_id=str(loan_id)),
        metadata=metadata,
    )
    assert [p.id for p in historial.payments] == [segundo.payment_id]
    assert list(historial.payments[0].covered_installments) == [1]
