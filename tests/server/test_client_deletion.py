"""BR-CLI-008: eliminación de un cliente cargado por error.

Archivo propio y no un bloque más en test_client_interceptor_integration.py,
mismo criterio que tests/server/test_loan_deletion.py (BR-LOAN-012): lo
interesante acá no es "quién puede llamarla" sino qué se lleva puesto la
cascada. A diferencia de DeleteLoan, DeleteClient no tiene ningún estado que
lo restrinja -- por decisión del negocio, un cliente se borra sin excepción,
así que la cobertura se concentra en que la cascada (préstamos, pagos,
ajustes de cuota) sea completa y en que un movimiento de caja ya imputado a
un pago sobreviva con `loan_payment_id` en null en vez de desaparecer.

Mismo patrón de servidor real + AuthInterceptor que el resto de las pruebas
de RBAC -- DeleteClient lee el actor desde el token para el AuditLog, así que
una llamada directa al servicer no ejercitaría el camino que importa.
"""

from concurrent import futures
from datetime import date, datetime, timezone
from decimal import Decimal

import auth_service_pb2
import auth_service_pb2_grpc
import client_service_pb2
import client_service_pb2_grpc
import grpc
import pytest

from cas_server.db.base import SessionLocal
from cas_server.db.models import (
    AuditLog,
    CashMovement,
    CashMovementTypeEnum,
    CashSession,
    Client,
    Loan,
    LoanInstallmentAdjustment,
    LoanPayment,
    LoanStatusEnum,
    RoleEnum,
    User,
)
from cas_server.security.interceptor import AuthInterceptor
from cas_server.security.passwords import hash_password
from cas_server.services.auth_service import AuthServicer
from cas_server.services.client_service import ClientServicer


@pytest.fixture
def stubs():
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=4), interceptors=[AuthInterceptor()]
    )
    auth_service_pb2_grpc.add_AuthServiceServicer_to_server(AuthServicer(), server)
    client_service_pb2_grpc.add_ClientServiceServicer_to_server(
        ClientServicer(), server
    )
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()

    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        yield (
            auth_service_pb2_grpc.AuthServiceStub(channel),
            client_service_pb2_grpc.ClientServiceStub(channel),
        )
    finally:
        channel.close()
        server.stop(grace=None)


def _create_user(username, role):
    with SessionLocal() as session:
        user = User(
            username=username,
            password_hash=hash_password("Passw0rd!"),
            role=role,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return user.id


def _create_client_row(national_id, email):
    with SessionLocal() as session:
        client = Client(
            first_name="Borrar",
            last_name="Cliente",
            national_id=national_id,
            email=email,
            phone_number="0981555555",
            date_of_birth=date(1990, 1, 1),
            address="Calle Borrar 1",
            declared_monthly_income=Decimal("2000.00"),
            is_active=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(client)
        session.commit()
        session.refresh(client)
        return client.id


def _create_loan_row(client_id, status):
    with SessionLocal() as session:
        loan = Loan(
            client_id=client_id,
            principal_amount=Decimal("1000.00"),
            interest_rate=Decimal("0.18"),
            term_months=6,
            first_due_date=date(2026, 1, 10),
            status=status,
            created_at=datetime.now(timezone.utc),
        )
        session.add(loan)
        session.commit()
        session.refresh(loan)
        return loan.id


def _create_payment_row(loan_id, amount=Decimal("100.00")):
    with SessionLocal() as session:
        payment = LoanPayment(
            loan_id=loan_id,
            amount=amount,
            transfer_reference="TRF-BORRAR",
            paid_at=datetime.now(timezone.utc),
        )
        session.add(payment)
        session.commit()
        session.refresh(payment)
        return payment.id


def _login(auth_stub, username):
    response = auth_stub.Login(
        auth_service_pb2.LoginRequest(username=username, password="Passw0rd!")
    )
    return (("authorization", f"Bearer {response.access_token}"),)


def _client_exists(client_id) -> bool:
    with SessionLocal() as session:
        return session.get(Client, client_id) is not None


def _loan_exists(loan_id) -> bool:
    with SessionLocal() as session:
        return session.get(Loan, loan_id) is not None


def _delete(client_id, reason="Cargado por error"):
    return client_service_pb2.DeleteClientRequest(
        client_id=str(client_id), reason=reason
    )


def test_delete_client_is_denied_to_the_teller(stubs):
    """El único rol sin el permiso es el cajero (BR-CAJA-005): ventanilla
    consulta y cobra, no origina ni deshace originación."""
    auth_stub, client_stub = stubs
    _create_user("cashier_cdel", RoleEnum.CASHIER)
    client_id = _create_client_row("8100000", "cdel0@example.com")

    with pytest.raises(grpc.RpcError) as exc_info:
        client_stub.DeleteClient(
            _delete(client_id), metadata=_login(auth_stub, "cashier_cdel")
        )
    assert exc_info.value.code() == grpc.StatusCode.PERMISSION_DENIED
    assert _client_exists(client_id)


def test_delete_client_is_allowed_from_credit_analyst_up(stubs):
    auth_stub, client_stub = stubs
    _create_user("analyst_cdel", RoleEnum.CREDIT_ANALYST)
    _create_user("manager_cdel", RoleEnum.MANAGER)

    for index, username in enumerate(("analyst_cdel", "manager_cdel")):
        client_id = _create_client_row(f"810000{index}", f"cdel_ok{index}@example.com")
        response = client_stub.DeleteClient(
            _delete(client_id), metadata=_login(auth_stub, username)
        )
        assert response.success is True, username
        assert response.deleted_loans_count == 0
        assert not _client_exists(client_id)


def test_delete_client_requires_a_reason(stubs):
    auth_stub, client_stub = stubs
    _create_user("manager_reason_cdel", RoleEnum.MANAGER)
    client_id = _create_client_row("8100002", "cdel2@example.com")

    with pytest.raises(grpc.RpcError) as exc_info:
        client_stub.DeleteClient(
            _delete(client_id, reason="   "),
            metadata=_login(auth_stub, "manager_reason_cdel"),
        )
    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert _client_exists(client_id)


def test_delete_client_on_a_missing_client_is_not_found(stubs):
    auth_stub, client_stub = stubs
    _create_user("manager_404_cdel", RoleEnum.MANAGER)

    with pytest.raises(grpc.RpcError) as exc_info:
        client_stub.DeleteClient(
            client_service_pb2.DeleteClientRequest(
                client_id="11111111-1111-1111-1111-111111111111", reason="No existe"
            ),
            metadata=_login(auth_stub, "manager_404_cdel"),
        )
    assert exc_info.value.code() == grpc.StatusCode.NOT_FOUND


def test_delete_client_rejects_a_malformed_id(stubs):
    auth_stub, client_stub = stubs
    _create_user("manager_uuid_cdel", RoleEnum.MANAGER)

    with pytest.raises(grpc.RpcError) as exc_info:
        client_stub.DeleteClient(
            client_service_pb2.DeleteClientRequest(
                client_id="no-es-un-uuid", reason="X"
            ),
            metadata=_login(auth_stub, "manager_uuid_cdel"),
        )
    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT


@pytest.mark.parametrize(
    "status",
    [
        LoanStatusEnum.PENDING,
        LoanStatusEnum.APPROVED,
        LoanStatusEnum.ACTIVE,
        LoanStatusEnum.PAID,
        LoanStatusEnum.DEFAULTED,
        LoanStatusEnum.EXPIRED,
    ],
)
def test_delete_client_accepts_every_loan_state_without_exception(stubs, status):
    """A diferencia de DeleteLoan, acá no hay estado que bloquee -- por
    decisión del negocio, un cliente se borra sin excepción, sin importar si
    alguno de sus préstamos ya movió dinero."""
    auth_stub, client_stub = stubs
    username = f"manager_any_{status.value.lower()}"
    _create_user(username, RoleEnum.MANAGER)
    client_id = _create_client_row(
        f"81001{status.value[:2]}", f"any_{status.value.lower()}@example.com"
    )
    loan_id = _create_loan_row(client_id, status)

    response = client_stub.DeleteClient(
        _delete(client_id), metadata=_login(auth_stub, username)
    )
    assert response.success is True
    assert response.deleted_loans_count == 1
    assert not _client_exists(client_id)
    assert not _loan_exists(loan_id)


def test_delete_client_removes_payments_and_installment_adjustments(stubs):
    auth_stub, client_stub = stubs
    _create_user("manager_cascade_cdel", RoleEnum.MANAGER)
    client_id = _create_client_row("8100030", "cascade@example.com")
    loan_id = _create_loan_row(client_id, LoanStatusEnum.ACTIVE)
    payment_id = _create_payment_row(loan_id)
    with SessionLocal() as session:
        session.add(
            LoanInstallmentAdjustment(
                loan_id=loan_id, installment_number=2, adjusted_amount=Decimal("50.00")
            )
        )
        session.commit()

    response = client_stub.DeleteClient(
        _delete(client_id), metadata=_login(auth_stub, "manager_cascade_cdel")
    )
    assert response.success is True

    with SessionLocal() as session:
        assert session.get(LoanPayment, payment_id) is None
        assert (
            session.query(LoanInstallmentAdjustment).filter_by(loan_id=loan_id).count()
            == 0
        )


def test_delete_client_preserves_a_cash_movement_but_clears_its_payment_link(stubs):
    """BR-CAJA-003: un movimiento de caja ya puede estar sumado en el arqueo
    de un turno cerrado y firmado. Borrar el cliente no debe hacerlo
    desaparecer ni cambiar su monto -- solo pierde el puntero hacia el pago
    que ya no existe."""
    auth_stub, client_stub = stubs
    user_id = _create_user("cajero_cdel", RoleEnum.CASHIER)
    client_id = _create_client_row("8100031", "cajamov@example.com")
    loan_id = _create_loan_row(client_id, LoanStatusEnum.ACTIVE)
    payment_id = _create_payment_row(loan_id)

    with SessionLocal() as session:
        cash_session = CashSession(
            user_id=user_id,
            opening_amount=Decimal("100000.00"),
            opened_at=datetime.now(timezone.utc),
        )
        session.add(cash_session)
        session.flush()
        movement = CashMovement(
            cash_session_id=cash_session.id,
            movement_type=CashMovementTypeEnum.INGRESO,
            amount=Decimal("100.00"),
            concept="Cobro de cuota",
            loan_payment_id=payment_id,
            created_by_user_id=user_id,
        )
        session.add(movement)
        session.commit()
        session.refresh(movement)
        movement_id = movement.id

    _create_user("manager_cajamov_cdel", RoleEnum.MANAGER)
    client_stub.DeleteClient(
        _delete(client_id), metadata=_login(auth_stub, "manager_cajamov_cdel")
    )

    with SessionLocal() as session:
        movement_after = session.get(CashMovement, movement_id)
        assert movement_after is not None
        assert movement_after.loan_payment_id is None
        assert movement_after.amount == Decimal("100.00")
        assert session.get(LoanPayment, payment_id) is None


def test_delete_client_records_a_self_contained_audit_entry(stubs):
    auth_stub, client_stub = stubs
    _create_user("manager_audit_cdel", RoleEnum.MANAGER)
    client_id = _create_client_row("8100032", "auditcdel@example.com")
    loan_id = _create_loan_row(client_id, LoanStatusEnum.PENDING)

    client_stub.DeleteClient(
        _delete(client_id, reason="Cliente duplicado"),
        metadata=_login(auth_stub, "manager_audit_cdel"),
    )

    with SessionLocal() as session:
        acciones = [
            row.action
            for row in session.query(AuditLog).all()
            if row.action.startswith("CLIENTE_ELIMINADO")
        ]
    assert len(acciones) == 1
    accion = acciones[0]
    assert f"client_id={client_id}" in accion
    assert "documento=8100032" in accion
    assert "prestamos_eliminados=1" in accion
    assert "motivo=Cliente duplicado" in accion
    assert not _loan_exists(loan_id)
