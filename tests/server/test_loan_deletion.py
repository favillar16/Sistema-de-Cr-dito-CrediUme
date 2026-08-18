"""BR-LOAN-012: eliminación de un préstamo cargado por error.

Archivo propio y no un bloque más en test_loan_interceptor_integration.py
porque es la única operación del sistema que borra una fila de negocio, y lo
que hay que probar acá no es sólo "quién puede llamarla" (el patrón de ese
archivo) sino sobre todo *qué préstamos* acepta borrar y qué invariante
protege: la plata ya cobrada no desaparece.

Mismo patrón de servidor real + AuthInterceptor que el resto de las pruebas de
RBAC -- DeleteLoan lee el actor desde el token para el AuditLog, así que una
llamada directa al servicer no ejercitaría el camino que importa.
"""

from concurrent import futures
from datetime import date, datetime, timezone
from decimal import Decimal

import auth_service_pb2
import auth_service_pb2_grpc
import grpc
import loan_service_pb2
import loan_service_pb2_grpc
import pytest

from cas_server.db.base import SessionLocal
from cas_server.db.models import (
    AuditLog,
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
from cas_server.services.loan_service import LoanServicer


@pytest.fixture
def stubs():
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=4), interceptors=[AuthInterceptor()]
    )
    auth_service_pb2_grpc.add_AuthServiceServicer_to_server(AuthServicer(), server)
    loan_service_pb2_grpc.add_LoanServiceServicer_to_server(LoanServicer(), server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()

    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        yield (
            auth_service_pb2_grpc.AuthServiceStub(channel),
            loan_service_pb2_grpc.LoanServiceStub(channel),
        )
    finally:
        channel.close()
        server.stop(grace=None)


def _create_user(username, role):
    with SessionLocal() as session:
        session.add(
            User(
                username=username,
                password_hash=hash_password("Passw0rd!"),
                role=role,
            )
        )
        session.commit()


def _create_client_row(national_id, email, income=Decimal("2000.00")):
    with SessionLocal() as session:
        client = Client(
            first_name="Borrar",
            last_name="Prestamo",
            national_id=national_id,
            email=email,
            phone_number="0981555555",
            date_of_birth=date(1990, 1, 1),
            address="Calle Borrar 1",
            declared_monthly_income=income,
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


def _login(auth_stub, username):
    response = auth_stub.Login(
        auth_service_pb2.LoginRequest(username=username, password="Passw0rd!")
    )
    return (("authorization", f"Bearer {response.access_token}"),)


def _loan_exists(loan_id) -> bool:
    with SessionLocal() as session:
        return session.get(Loan, loan_id) is not None


def _delete(loan_id, reason="Cargado por error"):
    return loan_service_pb2.DeleteLoanRequest(loan_id=str(loan_id), reason=reason)


def test_delete_loan_requires_manager_or_above(stubs):
    """El gate está un escalón por encima del de originación: un Analista de
    Crédito puede crear y aprobar, pero deshacer la carga de otro operador es
    una intervención de supervisión."""
    auth_stub, loan_stub = stubs
    _create_user("analyst_del", RoleEnum.CREDIT_ANALYST)
    _create_user("manager_del", RoleEnum.MANAGER)
    client_id = _create_client_row("7100001", "del1@example.com")
    loan_id = _create_loan_row(client_id, LoanStatusEnum.PENDING)

    with pytest.raises(grpc.RpcError) as exc_info:
        loan_stub.DeleteLoan(
            _delete(loan_id), metadata=_login(auth_stub, "analyst_del")
        )
    assert exc_info.value.code() == grpc.StatusCode.PERMISSION_DENIED
    assert _loan_exists(loan_id)

    response = loan_stub.DeleteLoan(
        _delete(loan_id), metadata=_login(auth_stub, "manager_del")
    )
    assert response.success is True
    assert response.client_id == str(client_id)
    assert response.deleted_status == "PENDING"
    assert not _loan_exists(loan_id)


def test_delete_loan_requires_a_reason(stubs):
    """Un motivo en blanco dejaría un AuditLog que no explica nada, y el
    préstamo que explicaría ya no existe."""
    auth_stub, loan_stub = stubs
    _create_user("manager_reason", RoleEnum.MANAGER)
    client_id = _create_client_row("7100002", "del2@example.com")
    loan_id = _create_loan_row(client_id, LoanStatusEnum.PENDING)

    with pytest.raises(grpc.RpcError) as exc_info:
        loan_stub.DeleteLoan(
            _delete(loan_id, reason="   "),
            metadata=_login(auth_stub, "manager_reason"),
        )
    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert _loan_exists(loan_id)


@pytest.mark.parametrize(
    "status",
    [LoanStatusEnum.PENDING, LoanStatusEnum.APPROVED, LoanStatusEnum.EXPIRED],
)
def test_delete_loan_accepts_every_state_that_never_moved_money(stubs, status):
    auth_stub, loan_stub = stubs
    username = f"manager_ok_{status.value.lower()}"
    _create_user(username, RoleEnum.MANAGER)
    client_id = _create_client_row(
        f"71001{status.value[:2]}", f"ok_{status.value.lower()}@example.com"
    )
    loan_id = _create_loan_row(client_id, status)

    response = loan_stub.DeleteLoan(
        _delete(loan_id), metadata=_login(auth_stub, username)
    )
    assert response.success is True
    assert response.deleted_status == status.value
    assert not _loan_exists(loan_id)


@pytest.mark.parametrize(
    "status",
    [LoanStatusEnum.ACTIVE, LoanStatusEnum.PAID, LoanStatusEnum.DEFAULTED],
)
def test_delete_loan_refuses_a_disbursed_loan(stubs, status):
    """ACTIVE/PAID/DEFAULTED implican un desembolso ya hecho: borrarlos podría
    hacer desaparecer pagos imputados a un turno de caja ya cerrado."""
    auth_stub, loan_stub = stubs
    username = f"manager_no_{status.value.lower()}"
    _create_user(username, RoleEnum.MANAGER)
    client_id = _create_client_row(
        f"71002{status.value[:2]}", f"no_{status.value.lower()}@example.com"
    )
    loan_id = _create_loan_row(client_id, status)

    with pytest.raises(grpc.RpcError) as exc_info:
        loan_stub.DeleteLoan(_delete(loan_id), metadata=_login(auth_stub, username))
    assert exc_info.value.code() == grpc.StatusCode.FAILED_PRECONDITION
    assert _loan_exists(loan_id)


def test_delete_loan_never_destroys_a_registered_payment(stubs):
    """El invariante que sostiene toda la regla: la plata cobrada no se borra.

    Se arma a propósito el caso que hoy el estado ya impediría (un préstamo
    APPROVED con un pago cargado) para probar que la verificación de pagos es
    real y no está viva sólo por casualidad, cubierta por la de estado.
    """
    auth_stub, loan_stub = stubs
    _create_user("manager_paid_del", RoleEnum.MANAGER)
    client_id = _create_client_row("7100030", "paydel@example.com")
    loan_id = _create_loan_row(client_id, LoanStatusEnum.APPROVED)
    with SessionLocal() as session:
        session.add(
            LoanPayment(
                loan_id=loan_id,
                amount=Decimal("100.00"),
                transfer_reference="TRF-NO-BORRAR",
                paid_at=datetime.now(timezone.utc),
            )
        )
        session.commit()

    with pytest.raises(grpc.RpcError) as exc_info:
        loan_stub.DeleteLoan(
            _delete(loan_id), metadata=_login(auth_stub, "manager_paid_del")
        )
    assert exc_info.value.code() == grpc.StatusCode.FAILED_PRECONDITION
    assert _loan_exists(loan_id)
    with SessionLocal() as session:
        assert session.query(LoanPayment).filter_by(loan_id=loan_id).count() == 1


def test_delete_loan_removes_its_installment_adjustments(stubs):
    """Las excepciones de cuota cuelgan del préstamo por FK -- si quedaran,
    el DELETE fallaría con un error de integridad en vez de una respuesta."""
    auth_stub, loan_stub = stubs
    _create_user("manager_adj_del", RoleEnum.MANAGER)
    client_id = _create_client_row("7100031", "adjdel@example.com")
    loan_id = _create_loan_row(client_id, LoanStatusEnum.PENDING)
    with SessionLocal() as session:
        session.add(
            LoanInstallmentAdjustment(
                loan_id=loan_id, installment_number=2, adjusted_amount=Decimal("50.00")
            )
        )
        session.commit()

    response = loan_stub.DeleteLoan(
        _delete(loan_id), metadata=_login(auth_stub, "manager_adj_del")
    )
    assert response.success is True
    with SessionLocal() as session:
        assert (
            session.query(LoanInstallmentAdjustment).filter_by(loan_id=loan_id).count()
            == 0
        )


def test_delete_loan_records_a_self_contained_audit_entry(stubs):
    """El préstamo desaparece, así que el AuditLog tiene que alcanzar por sí
    solo para reconstruir qué se borró y por qué."""
    auth_stub, loan_stub = stubs
    _create_user("manager_audit_del", RoleEnum.MANAGER)
    client_id = _create_client_row("7100032", "auditdel@example.com")
    loan_id = _create_loan_row(client_id, LoanStatusEnum.PENDING)

    loan_stub.DeleteLoan(
        _delete(loan_id, reason="Duplicado de la solicitud anterior"),
        metadata=_login(auth_stub, "manager_audit_del"),
    )

    with SessionLocal() as session:
        acciones = [
            row.action
            for row in session.query(AuditLog).all()
            if row.action.startswith("PRESTAMO_ELIMINADO")
        ]
    assert len(acciones) == 1
    accion = acciones[0]
    assert f"loan_id={loan_id}" in accion
    assert f"client_id={client_id}" in accion
    assert "estado=PENDING" in accion
    assert "capital=1000.00" in accion
    assert "cuotas=6" in accion
    assert "motivo=Duplicado de la solicitud anterior" in accion


def test_delete_loan_on_a_missing_loan_is_not_found(stubs):
    auth_stub, loan_stub = stubs
    _create_user("manager_404_del", RoleEnum.MANAGER)

    with pytest.raises(grpc.RpcError) as exc_info:
        loan_stub.DeleteLoan(
            loan_service_pb2.DeleteLoanRequest(
                loan_id="11111111-1111-1111-1111-111111111111", reason="No existe"
            ),
            metadata=_login(auth_stub, "manager_404_del"),
        )
    assert exc_info.value.code() == grpc.StatusCode.NOT_FOUND


def test_delete_loan_rejects_a_malformed_id(stubs):
    auth_stub, loan_stub = stubs
    _create_user("manager_uuid_del", RoleEnum.MANAGER)

    with pytest.raises(grpc.RpcError) as exc_info:
        loan_stub.DeleteLoan(
            loan_service_pb2.DeleteLoanRequest(loan_id="no-es-un-uuid", reason="X"),
            metadata=_login(auth_stub, "manager_uuid_del"),
        )
    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT


def test_delete_loan_frees_the_client_for_deactivation(stubs):
    """Cierra el círculo con BR-CLI-004: una solicitud cargada por error que
    terminó EXPIRED bloqueaba la baja del cliente para siempre, porque ese
    estado tampoco es PAID y no había forma de sacarla del sistema."""
    auth_stub, loan_stub = stubs
    _create_user("manager_free_del", RoleEnum.MANAGER)
    client_id = _create_client_row("7100033", "freedel@example.com")
    loan_id = _create_loan_row(client_id, LoanStatusEnum.EXPIRED)

    loan_stub.DeleteLoan(
        _delete(loan_id, reason="Solicitud inexistente"),
        metadata=_login(auth_stub, "manager_free_del"),
    )

    with SessionLocal() as session:
        restantes = (
            session.query(Loan)
            .filter(Loan.client_id == client_id, Loan.status != LoanStatusEnum.PAID)
            .count()
        )
    assert restantes == 0
