"""RBAC coverage for LoanService, mirroring test_interceptor_integration.py's
pattern: a real grpc.Server with AuthInterceptor wired in, driven over an
actual channel so metadata-based auth is exercised for real."""

from concurrent import futures
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import auth_service_pb2
import auth_service_pb2_grpc
import grpc
import loan_service_pb2
import loan_service_pb2_grpc
import pytest

from cas_server.config import LOAN_FIXED_INTEREST_RATE
from cas_server.db.base import SessionLocal
from cas_server.db.models import (
    AuditLog,
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
from cas_server.services.loan_service import LoanServicer

# BR-LOAN-007: la tasa que un rol Estándar tiene permitido mandar. Se lee de la
# config en vez de repetirse a mano acá para que un cambio de tasa (p. ej. el
# paso de 24% a 18% anual) no rompa media docena de tests que sólo la usaban
# como "una tasa cualquiera que el servidor debería aceptar".
_TASA_ESTANDAR = str(LOAN_FIXED_INTEREST_RATE)


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
    auth_stub = auth_service_pb2_grpc.AuthServiceStub(channel)
    loan_stub = loan_service_pb2_grpc.LoanServiceStub(channel)
    try:
        yield auth_stub, loan_stub
    finally:
        channel.close()
        server.stop(grace=None)


def _create_user(
    username, password, role, first_name=None, last_name=None, national_id=None
):
    """Los datos personales (BR-AUTH-006) son opcionales acá a propósito: la
    mayoría de los tests no los necesitan, y dejarlos en None cubre de paso el
    caso de los usuarios que ya existían antes de esos campos."""
    with SessionLocal() as session:
        session.add(
            User(
                username=username,
                password_hash=hash_password(password),
                role=role,
                first_name=first_name,
                last_name=last_name,
                national_id=national_id,
            )
        )
        session.commit()


def _create_client_row(national_id="7000001", email="loanrbac@example.com"):
    with SessionLocal() as session:
        client = Client(
            first_name="Loan",
            last_name="RBAC",
            national_id=national_id,
            email=email,
            phone_number="0981444444",
            date_of_birth=date(1990, 1, 1),
            address="Calle Loan RBAC 1",
            declared_monthly_income=Decimal("2000.00"),
            is_active=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(client)
        session.commit()
        session.refresh(client)
        return client.id


def _create_loan_row(client_id, status, approved_at=None):
    with SessionLocal() as session:
        loan = Loan(
            client_id=client_id,
            principal_amount=Decimal("1000.00"),
            interest_rate=Decimal("0.10"),
            term_months=6,
            first_due_date=datetime.now(timezone.utc).date(),
            status=status,
            created_at=datetime.now(timezone.utc),
            approved_at=approved_at,
        )
        session.add(loan)
        session.commit()
        session.refresh(loan)
        return loan.id


def _login(auth_stub, username, password):
    response = auth_stub.Login(
        auth_service_pb2.LoginRequest(username=username, password=password)
    )
    return (("authorization", f"Bearer {response.access_token}"),)


def test_create_loan_requires_credit_analyst_or_above(stubs):
    """BR-CAJA-005: originar un crédito dejó de estar al alcance del cajero
    cuando el rol volvió a usarse como ventanilla."""
    auth_stub, loan_stub = stubs
    _create_user("cashier_l", "Passw0rd!", RoleEnum.CASHIER)
    _create_user("analyst_l", "Passw0rd!", RoleEnum.CREDIT_ANALYST)
    client_id = _create_client_row(
        national_id="7000002", email="cashier_loan@example.com"
    )

    solicitud = loan_service_pb2.CreateLoanRequest(
        client_id=str(client_id),
        principal_amount="1000.00",
        interest_rate=_TASA_ESTANDAR,
        term_months=6,
    )

    cashier_metadata = _login(auth_stub, "cashier_l", "Passw0rd!")
    with pytest.raises(grpc.RpcError) as exc_info:
        loan_stub.CreateLoan(solicitud, metadata=cashier_metadata)
    assert exc_info.value.code() == grpc.StatusCode.PERMISSION_DENIED

    analyst_metadata = _login(auth_stub, "analyst_l", "Passw0rd!")
    response = loan_stub.CreateLoan(solicitud, metadata=analyst_metadata)
    assert response.loan_id


def test_create_loan_rejects_non_standard_rate_for_standard_role(stubs):
    # BR-LOAN-007 se define contra "roles por debajo de MANAGER"; desde
    # BR-CAJA-005 el rol más bajo que puede crear un préstamo es
    # CREDIT_ANALYST, así que la prueba usa ese (antes usaba CASHIER, que hoy
    # fallaría antes por PERMISSION_DENIED y no probaría la regla de tasa).
    auth_stub, loan_stub = stubs
    _create_user("analyst_r", "Passw0rd!", RoleEnum.CREDIT_ANALYST)
    client_id = _create_client_row(national_id="7000008", email="rate_std@example.com")
    metadata = _login(auth_stub, "analyst_r", "Passw0rd!")

    with pytest.raises(grpc.RpcError) as exc_info:
        loan_stub.CreateLoan(
            loan_service_pb2.CreateLoanRequest(
                client_id=str(client_id),
                principal_amount="1000.00",
                interest_rate="0.10",
                term_months=6,
            ),
            metadata=metadata,
        )
    assert exc_info.value.code() == grpc.StatusCode.FAILED_PRECONDITION


def test_create_loan_rejects_a_custom_rate_even_from_a_manager(stubs):
    """BR-LOAN-007 dejó de tener excepción por rol (2026-08-28).

    Este test decía lo contrario hasta esa fecha: se reescribió, no se borró,
    porque su nombre en el historial ("allows_manager_to_set_custom_rate") es
    justamente lo que llevaría a "restaurar" la excepción creyendo que se
    perdió. La tasa es una condición comercial de la entidad, no algo que se
    negocie préstamo por préstamo.
    """
    auth_stub, loan_stub = stubs
    _create_user("manager_r", "Passw0rd!", RoleEnum.MANAGER)
    client_id = _create_client_row(
        national_id="7000009", email="rate_manager@example.com"
    )
    metadata = _login(auth_stub, "manager_r", "Passw0rd!")

    with pytest.raises(grpc.RpcError) as exc_info:
        loan_stub.CreateLoan(
            loan_service_pb2.CreateLoanRequest(
                client_id=str(client_id),
                principal_amount="1000.00",
                interest_rate="0.10",
                term_months=6,
            ),
            metadata=metadata,
        )
    assert exc_info.value.code() == grpc.StatusCode.FAILED_PRECONDITION


def test_create_loan_stores_the_charges_sent_with_the_proposal(stubs):
    """La propuesta viaja completa en una sola llamada: los cargos ya no
    necesitan un UpdateLoanCharges posterior (y no deben, porque determinan la
    cuota que valida BR-LOAN-002)."""
    auth_stub, loan_stub = stubs
    _create_user("analyst_c", "Passw0rd!", RoleEnum.CREDIT_ANALYST)
    client_id = _create_client_row(
        national_id="7000020", email="charges_create@example.com"
    )
    metadata = _login(auth_stub, "analyst_c", "Passw0rd!")

    # BR-LOAN-006 (revisado 2026-09-08): tope de 1000.00 * 0.40 * 6/12 =
    # 200.00 -- los 120.00 de acá quedan por debajo.
    creado = loan_stub.CreateLoan(
        loan_service_pb2.CreateLoanRequest(
            client_id=str(client_id),
            principal_amount="1000.00",
            term_months=6,
            charge_admin_fee="75.00",
            charge_contracted_insurance="45.00",
            guarantee_type="SOLA FIRMA",
            guarantee_amount="1500.00",
        ),
        metadata=metadata,
    )
    detalle = loan_stub.GetLoanById(
        loan_service_pb2.GetLoanByIdRequest(loan_id=creado.loan_id), metadata=metadata
    )
    assert detalle.total_charges == "120.00"
    assert detalle.total_credit_with_charges == "1120.00"
    assert detalle.amount_to_disburse == "1000.00"
    assert detalle.guarantee_type == "SOLA FIRMA"


def test_update_loan_proposal_allows_credit_analyst(stubs):
    auth_stub, loan_stub = stubs
    _create_user("analyst_u", "Passw0rd!", RoleEnum.CREDIT_ANALYST)
    client_id = _create_client_row(
        national_id="7000005", email="update_proposal@example.com"
    )
    loan_id = _create_loan_row(client_id, LoanStatusEnum.PENDING)
    metadata = _login(auth_stub, "analyst_u", "Passw0rd!")

    response = loan_stub.UpdateLoanProposal(
        loan_service_pb2.UpdateLoanProposalRequest(
            loan_id=str(loan_id),
            principal_amount="1200.00",
            term_months=8,
            # Derivada de hoy y no una fecha fija: el préstamo se crea con
            # created_at=now(), y el servidor rechaza un primer vencimiento
            # anterior a esa fecha -- con un literal, el test pasa hasta que
            # el calendario lo alcanza y después falla solo.
            first_due_date=(
                datetime.now(timezone.utc).date() + timedelta(days=30)
            ).isoformat(),
        ),
        metadata=metadata,
    )
    assert response.success
    assert response.status == "PENDING"


def test_update_loan_guarantee_allows_credit_analyst(stubs):
    auth_stub, loan_stub = stubs
    _create_user("analyst_g", "Passw0rd!", RoleEnum.CREDIT_ANALYST)
    client_id = _create_client_row(
        national_id="7000006", email="update_guarantee@example.com"
    )
    loan_id = _create_loan_row(client_id, LoanStatusEnum.PENDING)
    metadata = _login(auth_stub, "analyst_g", "Passw0rd!")

    response = loan_stub.UpdateLoanGuarantee(
        loan_service_pb2.UpdateLoanGuaranteeRequest(
            loan_id=str(loan_id),
            guarantee_type="SOLA FIRMA",
            guarantee_amount="1000.00",
        ),
        metadata=metadata,
    )
    assert response.success
    assert response.status == "PENDING"


def test_update_loan_charges_allows_credit_analyst(stubs):
    auth_stub, loan_stub = stubs
    _create_user("analyst_c", "Passw0rd!", RoleEnum.CREDIT_ANALYST)
    client_id = _create_client_row(
        national_id="7000007", email="update_charges@example.com"
    )
    loan_id = _create_loan_row(client_id, LoanStatusEnum.PENDING)
    metadata = _login(auth_stub, "analyst_c", "Passw0rd!")

    response = loan_stub.UpdateLoanCharges(
        loan_service_pb2.UpdateLoanChargesRequest(
            loan_id=str(loan_id), charge_admin_fee="15.00"
        ),
        metadata=metadata,
    )
    assert response.success
    assert response.status == "PENDING"
    assert response.total_charges == "15.00"


def test_approve_loan_requires_credit_analyst_or_above(stubs):
    auth_stub, loan_stub = stubs
    _create_user("cashier_a", "Passw0rd!", RoleEnum.CASHIER)
    _create_user("analyst_a", "Passw0rd!", RoleEnum.CREDIT_ANALYST)
    client_id = _create_client_row(national_id="7000003", email="approve@example.com")
    loan_id = _create_loan_row(client_id, LoanStatusEnum.PENDING)

    cashier_metadata = _login(auth_stub, "cashier_a", "Passw0rd!")
    with pytest.raises(grpc.RpcError) as exc_info:
        loan_stub.ApproveLoan(
            loan_service_pb2.ApproveLoanRequest(loan_id=str(loan_id)),
            metadata=cashier_metadata,
        )
    assert exc_info.value.code() == grpc.StatusCode.PERMISSION_DENIED

    analyst_metadata = _login(auth_stub, "analyst_a", "Passw0rd!")
    response = loan_stub.ApproveLoan(
        loan_service_pb2.ApproveLoanRequest(loan_id=str(loan_id)),
        metadata=analyst_metadata,
    )
    assert response.success


def test_disburse_loan_requires_manager_or_above(stubs):
    auth_stub, loan_stub = stubs
    _create_user("cashier_b", "Passw0rd!", RoleEnum.CASHIER)
    _create_user("analyst_b", "Passw0rd!", RoleEnum.CREDIT_ANALYST)
    _create_user("manager_b", "Passw0rd!", RoleEnum.MANAGER)
    client_id = _create_client_row(national_id="7000004", email="disburse@example.com")
    loan_id = _create_loan_row(
        client_id, LoanStatusEnum.APPROVED, approved_at=datetime.now(timezone.utc)
    )

    for username in ("cashier_b", "analyst_b"):
        metadata = _login(auth_stub, username, "Passw0rd!")
        with pytest.raises(grpc.RpcError) as exc_info:
            loan_stub.DisburseLoan(
                loan_service_pb2.DisburseLoanRequest(loan_id=str(loan_id)),
                metadata=metadata,
            )
        assert exc_info.value.code() == grpc.StatusCode.PERMISSION_DENIED

    manager_metadata = _login(auth_stub, "manager_b", "Passw0rd!")
    response = loan_stub.DisburseLoan(
        loan_service_pb2.DisburseLoanRequest(loan_id=str(loan_id)),
        metadata=manager_metadata,
    )
    assert response.success


def test_update_installment_amount_requires_manager_or_above(stubs):
    auth_stub, loan_stub = stubs
    _create_user("analyst_i", "Passw0rd!", RoleEnum.CREDIT_ANALYST)
    _create_user("manager_i", "Passw0rd!", RoleEnum.MANAGER)
    client_id = _create_client_row(
        national_id="7000010", email="installment@example.com"
    )
    loan_id = _create_loan_row(
        client_id, LoanStatusEnum.ACTIVE, approved_at=datetime.now(timezone.utc)
    )

    analyst_metadata = _login(auth_stub, "analyst_i", "Passw0rd!")
    with pytest.raises(grpc.RpcError) as exc_info:
        loan_stub.UpdateInstallmentAmount(
            loan_service_pb2.UpdateInstallmentAmountRequest(
                loan_id=str(loan_id), installment_number=1, adjusted_amount="150.00"
            ),
            metadata=analyst_metadata,
        )
    assert exc_info.value.code() == grpc.StatusCode.PERMISSION_DENIED

    manager_metadata = _login(auth_stub, "manager_i", "Passw0rd!")
    response = loan_stub.UpdateInstallmentAmount(
        loan_service_pb2.UpdateInstallmentAmountRequest(
            loan_id=str(loan_id), installment_number=1, adjusted_amount="150.00"
        ),
        metadata=manager_metadata,
    )
    assert response.success


def test_remove_installment_adjustment_requires_manager_or_above(stubs):
    """BR-LOAN-015: mismo rango que UpdateInstallmentAmount -- si el analista
    pudiera quitar ajustes sin poder ponerlos (o al revés) el flujo quedaría
    con una sola dirección otra vez."""
    auth_stub, loan_stub = stubs
    _create_user("analyst_r", "Passw0rd!", RoleEnum.CREDIT_ANALYST)
    _create_user("manager_r", "Passw0rd!", RoleEnum.MANAGER)
    client_id = _create_client_row(
        national_id="7000011", email="removeadjust@example.com"
    )
    loan_id = _create_loan_row(
        client_id, LoanStatusEnum.ACTIVE, approved_at=datetime.now(timezone.utc)
    )

    manager_metadata = _login(auth_stub, "manager_r", "Passw0rd!")
    loan_stub.UpdateInstallmentAmount(
        loan_service_pb2.UpdateInstallmentAmountRequest(
            loan_id=str(loan_id), installment_number=1, adjusted_amount="150.00"
        ),
        metadata=manager_metadata,
    )

    analyst_metadata = _login(auth_stub, "analyst_r", "Passw0rd!")
    with pytest.raises(grpc.RpcError) as exc_info:
        loan_stub.RemoveInstallmentAdjustment(
            loan_service_pb2.RemoveInstallmentAdjustmentRequest(
                loan_id=str(loan_id), installment_number=1, reason="prueba"
            ),
            metadata=analyst_metadata,
        )
    assert exc_info.value.code() == grpc.StatusCode.PERMISSION_DENIED

    response = loan_stub.RemoveInstallmentAdjustment(
        loan_service_pb2.RemoveInstallmentAdjustmentRequest(
            loan_id=str(loan_id), installment_number=1, reason="prueba"
        ),
        metadata=manager_metadata,
    )
    assert response.success


def test_get_loan_by_id_reports_creating_advisor(stubs):
    """created_by_username is set from whoever authenticated CreateLoan --
    used by the client to print an advisor name on the cronograma de pago
    handed to the client (see documents.py's cronograma_html)."""
    auth_stub, loan_stub = stubs
    _create_user("analyst_adv", "Passw0rd!", RoleEnum.CREDIT_ANALYST)
    client_id = _create_client_row(national_id="7000011", email="advisor@example.com")
    metadata = _login(auth_stub, "analyst_adv", "Passw0rd!")

    created = loan_stub.CreateLoan(
        loan_service_pb2.CreateLoanRequest(
            client_id=str(client_id),
            principal_amount="1000.00",
            interest_rate=_TASA_ESTANDAR,
            term_months=6,
        ),
        metadata=metadata,
    )

    detail = loan_stub.GetLoanById(
        loan_service_pb2.GetLoanByIdRequest(loan_id=created.loan_id),
        metadata=metadata,
    )
    assert detail.created_by_username == "analyst_adv"


def test_get_loan_by_id_reports_no_advisor_for_loan_without_creator(stubs):
    """Loans inserted without created_by_user_id (e.g. rows that predate
    this column) report "" rather than erroring."""
    auth_stub, loan_stub = stubs
    _create_user("cashier_noadv", "Passw0rd!", RoleEnum.CASHIER)
    client_id = _create_client_row(national_id="7000012", email="noadvisor@example.com")
    loan_id = _create_loan_row(client_id, LoanStatusEnum.PENDING)
    metadata = _login(auth_stub, "cashier_noadv", "Passw0rd!")

    detail = loan_stub.GetLoanById(
        loan_service_pb2.GetLoanByIdRequest(loan_id=str(loan_id)),
        metadata=metadata,
    )
    assert detail.created_by_username == ""


def test_record_payment_without_token_is_unauthenticated(stubs):
    _, loan_stub = stubs
    with pytest.raises(grpc.RpcError) as exc_info:
        loan_stub.RecordPayment(
            loan_service_pb2.RecordPaymentRequest(
                loan_id="00000000-0000-0000-0000-000000000000", amount="10.00"
            )
        )
    assert exc_info.value.code() == grpc.StatusCode.UNAUTHENTICATED


# ---- BR-AUTH-006 / BR-LOAN-011: identificación del operador ---------------


def test_get_loan_by_id_reports_advisor_personal_data(stubs):
    """BR-AUTH-006: el Cronograma de Pago identifica al asesor por nombre y
    C.I., no solo por su usuario del sistema."""
    auth_stub, loan_stub = stubs
    _create_user(
        "analyst_named",
        "Passw0rd!",
        RoleEnum.CREDIT_ANALYST,
        first_name="Ana",
        last_name="Benítez",
        national_id="4123456",
    )
    client_id = _create_client_row(national_id="7000020", email="named@example.com")
    metadata = _login(auth_stub, "analyst_named", "Passw0rd!")

    created = loan_stub.CreateLoan(
        loan_service_pb2.CreateLoanRequest(
            client_id=str(client_id),
            principal_amount="1000.00",
            interest_rate=_TASA_ESTANDAR,
            term_months=6,
        ),
        metadata=metadata,
    )
    detail = loan_stub.GetLoanById(
        loan_service_pb2.GetLoanByIdRequest(loan_id=created.loan_id), metadata=metadata
    )
    assert detail.created_by_full_name == "Ana Benítez"
    assert detail.created_by_national_id == "4123456"
    assert detail.created_by_username == "analyst_named"


def test_get_loan_by_id_advisor_personal_data_empty_for_legacy_user(stubs):
    """Un operador sin datos personales cargados devuelve "" en los campos
    nuevos -- el documento cae de vuelta a created_by_username."""
    auth_stub, loan_stub = stubs
    _create_user("analyst_unnamed", "Passw0rd!", RoleEnum.CREDIT_ANALYST)
    client_id = _create_client_row(national_id="7000021", email="unnamed@example.com")
    metadata = _login(auth_stub, "analyst_unnamed", "Passw0rd!")

    created = loan_stub.CreateLoan(
        loan_service_pb2.CreateLoanRequest(
            client_id=str(client_id),
            principal_amount="1000.00",
            interest_rate=_TASA_ESTANDAR,
            term_months=6,
        ),
        metadata=metadata,
    )
    detail = loan_stub.GetLoanById(
        loan_service_pb2.GetLoanByIdRequest(loan_id=created.loan_id), metadata=metadata
    )
    assert detail.created_by_full_name == ""
    assert detail.created_by_national_id == ""
    assert detail.created_by_username == "analyst_unnamed"


def test_record_payment_reports_the_operator_who_registered_it(stubs):
    """BR-LOAN-011: el "Registrado por" del Comprobante de Pago sale del
    usuario autenticado, no de lo que mande el cliente."""
    auth_stub, loan_stub = stubs
    _create_user(
        "manager_pay",
        "Passw0rd!",
        RoleEnum.MANAGER,
        first_name="Carlos",
        last_name="Duarte",
        national_id="3987654",
    )
    client_id = _create_client_row(national_id="7000022", email="paidby@example.com")
    loan_id = _create_loan_row(
        client_id, LoanStatusEnum.ACTIVE, approved_at=datetime.now(timezone.utc)
    )
    metadata = _login(auth_stub, "manager_pay", "Passw0rd!")

    response = loan_stub.RecordPayment(
        loan_service_pb2.RecordPaymentRequest(
            loan_id=str(loan_id),
            transfer_reference="TRF-COMPROBANTE",
            installment_number=1,
        ),
        metadata=metadata,
    )
    assert response.recorded_by_name == "Carlos Duarte"
    assert response.recorded_by_national_id == "3987654"
    assert list(response.covered_installments) == [1]
    assert response.transfer_reference == "TRF-COMPROBANTE"
    assert response.paid_at.seconds > 0


def test_record_payment_falls_back_to_username_when_operator_has_no_name(stubs):
    """El comprobante nunca sale con el campo "Registrado por" vacío."""
    auth_stub, loan_stub = stubs
    _create_user("manager_anon", "Passw0rd!", RoleEnum.MANAGER)
    client_id = _create_client_row(national_id="7000023", email="anonpay@example.com")
    loan_id = _create_loan_row(
        client_id, LoanStatusEnum.ACTIVE, approved_at=datetime.now(timezone.utc)
    )
    metadata = _login(auth_stub, "manager_anon", "Passw0rd!")

    response = loan_stub.RecordPayment(
        loan_service_pb2.RecordPaymentRequest(
            loan_id=str(loan_id), transfer_reference="TRF-ANON", installment_number=1
        ),
        metadata=metadata,
    )
    assert response.recorded_by_name == "manager_anon"
    assert response.recorded_by_national_id == ""


# --- BR-LOAN-014: reversión de un incumplimiento ------------------------------


def _estado_prestamo(loan_id):
    with SessionLocal() as session:
        return session.get(Loan, loan_id).status


def test_revert_default_requires_credit_analyst_or_above(stubs):
    """BR-LOAN-014: mismo nivel que MarkDefaulted -- quien puede poner la marca
    puede sacarla. El cajero cobra, no decide el estado del préstamo."""
    auth_stub, loan_stub = stubs
    _create_user("cashier_rd", "Passw0rd!", RoleEnum.CASHIER)
    _create_user("analyst_rd", "Passw0rd!", RoleEnum.CREDIT_ANALYST)
    client_id = _create_client_row(national_id="7000031", email="revert1@example.com")
    loan_id = _create_loan_row(client_id, LoanStatusEnum.DEFAULTED)

    with pytest.raises(grpc.RpcError) as exc_info:
        loan_stub.RevertDefault(
            loan_service_pb2.RevertDefaultRequest(
                loan_id=str(loan_id), reason="regularizó"
            ),
            metadata=_login(auth_stub, "cashier_rd", "Passw0rd!"),
        )
    assert exc_info.value.code() == grpc.StatusCode.PERMISSION_DENIED
    assert _estado_prestamo(loan_id) == LoanStatusEnum.DEFAULTED

    response = loan_stub.RevertDefault(
        loan_service_pb2.RevertDefaultRequest(
            loan_id=str(loan_id), reason="regularizó"
        ),
        metadata=_login(auth_stub, "analyst_rd", "Passw0rd!"),
    )
    assert response.success
    assert response.status == "ACTIVE"
    assert _estado_prestamo(loan_id) == LoanStatusEnum.ACTIVE


def test_revert_default_requires_a_reason(stubs):
    """El motivo es lo único que explica por qué se deshizo el juicio de otro
    operador sobre la cobrabilidad, así que un motivo en blanco se rechaza."""
    auth_stub, loan_stub = stubs
    _create_user("analyst_rd2", "Passw0rd!", RoleEnum.CREDIT_ANALYST)
    client_id = _create_client_row(national_id="7000032", email="revert2@example.com")
    loan_id = _create_loan_row(client_id, LoanStatusEnum.DEFAULTED)

    metadata = _login(auth_stub, "analyst_rd2", "Passw0rd!")
    for motivo in ("", "   "):
        with pytest.raises(grpc.RpcError) as exc_info:
            loan_stub.RevertDefault(
                loan_service_pb2.RevertDefaultRequest(
                    loan_id=str(loan_id), reason=motivo
                ),
                metadata=metadata,
            )
        assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT
    assert _estado_prestamo(loan_id) == LoanStatusEnum.DEFAULTED


@pytest.mark.parametrize(
    "estado",
    [
        LoanStatusEnum.PENDING,
        LoanStatusEnum.APPROVED,
        LoanStatusEnum.ACTIVE,
        LoanStatusEnum.PAID,
        LoanStatusEnum.EXPIRED,
    ],
)
def test_revert_default_only_applies_to_a_defaulted_loan(stubs, estado):
    auth_stub, loan_stub = stubs
    _create_user(f"analyst_rd_{estado.value}", "Passw0rd!", RoleEnum.CREDIT_ANALYST)
    client_id = _create_client_row(
        national_id=f"70001{estado.value[:2]}", email=f"rev{estado.value}@example.com"
    )
    loan_id = _create_loan_row(client_id, estado)

    with pytest.raises(grpc.RpcError) as exc_info:
        loan_stub.RevertDefault(
            loan_service_pb2.RevertDefaultRequest(
                loan_id=str(loan_id), reason="prueba"
            ),
            metadata=_login(auth_stub, f"analyst_rd_{estado.value}", "Passw0rd!"),
        )
    assert exc_info.value.code() == grpc.StatusCode.FAILED_PRECONDITION
    assert _estado_prestamo(loan_id) == estado


def test_revert_default_audits_the_reason(stubs):
    """La fila del préstamo no desaparece (a diferencia de DeleteLoan), pero el
    motivo sigue siendo lo único que explica la decisión."""
    auth_stub, loan_stub = stubs
    _create_user("analyst_rd3", "Passw0rd!", RoleEnum.CREDIT_ANALYST)
    client_id = _create_client_row(national_id="7000034", email="revert4@example.com")
    loan_id = _create_loan_row(client_id, LoanStatusEnum.DEFAULTED)

    loan_stub.RevertDefault(
        loan_service_pb2.RevertDefaultRequest(
            loan_id=str(loan_id), reason="acuerdo de pago firmado"
        ),
        metadata=_login(auth_stub, "analyst_rd3", "Passw0rd!"),
    )

    with SessionLocal() as session:
        acciones = [
            fila.action
            for fila in session.query(AuditLog).all()
            if fila.action.startswith("PRESTAMO_INCUMPLIMIENTO_REVERTIDO")
        ]
    assert len(acciones) == 1
    assert str(loan_id) in acciones[0]
    assert "acuerdo de pago firmado" in acciones[0]


def test_reverting_a_default_makes_the_loan_collectable_again(stubs):
    """La razón de ser de BR-LOAN-014.

    RecordPayment exige ACTIVE, así que mientras el préstamo esté DEFAULTED no
    se le puede cobrar ni cuando el cliente regulariza -- y DeleteLoan tampoco
    lo acepta, porque ya movió dinero. Sin la reversión, el préstamo queda
    congelado para siempre. Este test recorre justamente ese callejón: cobro
    rechazado, reversión, cobro aceptado.
    """
    auth_stub, loan_stub = stubs
    _create_user("analyst_rd4", "Passw0rd!", RoleEnum.CREDIT_ANALYST)
    client_id = _create_client_row(national_id="7000035", email="revert5@example.com")
    loan_id = _create_loan_row(client_id, LoanStatusEnum.DEFAULTED)
    metadata = _login(auth_stub, "analyst_rd4", "Passw0rd!")

    pago = loan_service_pb2.RecordPaymentRequest(
        loan_id=str(loan_id),
        amount="100.00",
        transfer_reference="TRF-REVERT-1",
    )

    with pytest.raises(grpc.RpcError) as exc_info:
        loan_stub.RecordPayment(pago, metadata=metadata)
    assert exc_info.value.code() == grpc.StatusCode.FAILED_PRECONDITION

    # Y tampoco se puede borrar: BR-LOAN-012 excluye los estados que ya
    # movieron dinero, así que sin RevertDefault no queda ninguna salida.
    with pytest.raises(grpc.RpcError) as exc_info:
        loan_stub.DeleteLoan(
            loan_service_pb2.DeleteLoanRequest(
                loan_id=str(loan_id), reason="intento de salida"
            ),
            metadata=metadata,
        )
    assert exc_info.value.code() == grpc.StatusCode.FAILED_PRECONDITION

    loan_stub.RevertDefault(
        loan_service_pb2.RevertDefaultRequest(
            loan_id=str(loan_id), reason="el cliente se puso al día"
        ),
        metadata=metadata,
    )

    respuesta = loan_stub.RecordPayment(pago, metadata=metadata)
    assert respuesta.amount_paid == "100.00"


def test_revert_default_on_unknown_loan_is_not_found(stubs):
    auth_stub, loan_stub = stubs
    _create_user("analyst_rd5", "Passw0rd!", RoleEnum.CREDIT_ANALYST)

    with pytest.raises(grpc.RpcError) as exc_info:
        loan_stub.RevertDefault(
            loan_service_pb2.RevertDefaultRequest(
                loan_id="11111111-1111-1111-1111-111111111111", reason="x"
            ),
            metadata=_login(auth_stub, "analyst_rd5", "Passw0rd!"),
        )
    assert exc_info.value.code() == grpc.StatusCode.NOT_FOUND


# --- BR-LOAN-016: historial de cobros y reimpresión ---------------------------
#
# Un cobro dejó de ser algo que sólo ve quien lo registra: el resto del
# personal lo consulta desde el préstamo, y el comprobante se puede volver a
# emitir. Eso obliga a que el historial reconstruya, para cada pago, las
# mismas cifras que devolvió RecordPayment en su momento -- si no, el papel
# reimpreso diría algo distinto del que se entregó.


def test_list_loan_payments_is_open_to_every_authenticated_role(stubs):
    """En ventanilla "¿ya pagué?" la pregunta el cliente y la contesta el
    cajero: el historial es consulta operativa, no material de gestión."""
    auth_stub, loan_stub = stubs
    _create_user("cashier_hist", "Passw0rd!", RoleEnum.CASHIER)
    client_id = _create_client_row(national_id="7000040", email="hist1@example.com")
    loan_id = _create_loan_row(
        client_id, LoanStatusEnum.ACTIVE, approved_at=datetime.now(timezone.utc)
    )
    metadata = _login(auth_stub, "cashier_hist", "Passw0rd!")

    response = loan_stub.ListLoanPayments(
        loan_service_pb2.ListLoanPaymentsRequest(loan_id=str(loan_id)),
        metadata=metadata,
    )

    assert list(response.payments) == []
    # 6 = term_months de _create_loan_row; es el "de 6" que imprime el
    # comprobante ("Cuota(s) 1 de 6").
    assert response.total_installments == 6


def test_list_loan_payments_without_token_is_unauthenticated(stubs):
    auth_stub, loan_stub = stubs
    with pytest.raises(grpc.RpcError) as exc_info:
        loan_stub.ListLoanPayments(
            loan_service_pb2.ListLoanPaymentsRequest(
                loan_id="11111111-1111-1111-1111-111111111111"
            )
        )
    assert exc_info.value.code() == grpc.StatusCode.UNAUTHENTICATED


def test_record_payment_returns_the_id_of_the_payment_it_created(stubs):
    """Sin el id no hay forma de volver a pedir ESE cobro: es lo que ata el
    comprobante emitido en el momento con el que se reimprime después."""
    auth_stub, loan_stub = stubs
    _create_user("manager_pid", "Passw0rd!", RoleEnum.MANAGER)
    client_id = _create_client_row(national_id="7000041", email="hist2@example.com")
    loan_id = _create_loan_row(
        client_id, LoanStatusEnum.ACTIVE, approved_at=datetime.now(timezone.utc)
    )
    metadata = _login(auth_stub, "manager_pid", "Passw0rd!")

    pago = loan_stub.RecordPayment(
        loan_service_pb2.RecordPaymentRequest(
            loan_id=str(loan_id), transfer_reference="TRF-ID", installment_number=1
        ),
        metadata=metadata,
    )
    historial = loan_stub.ListLoanPayments(
        loan_service_pb2.ListLoanPaymentsRequest(loan_id=str(loan_id)),
        metadata=metadata,
    )

    assert pago.payment_id
    assert [entrada.id for entrada in historial.payments] == [pago.payment_id]


def test_list_loan_payments_repeats_what_each_receipt_said(stubs):
    """El corazón de la reimpresión: cada entrada trae la imputación y el
    saldo *de ese pago*, no los del préstamo hoy."""
    auth_stub, loan_stub = stubs
    _create_user("manager_hist", "Passw0rd!", RoleEnum.MANAGER)
    client_id = _create_client_row(national_id="7000042", email="hist3@example.com")
    loan_id = _create_loan_row(
        client_id, LoanStatusEnum.ACTIVE, approved_at=datetime.now(timezone.utc)
    )
    metadata = _login(auth_stub, "manager_hist", "Passw0rd!")

    primero = loan_stub.RecordPayment(
        loan_service_pb2.RecordPaymentRequest(
            loan_id=str(loan_id), transfer_reference="TRF-1", installment_number=1
        ),
        metadata=metadata,
    )
    segundo = loan_stub.RecordPayment(
        loan_service_pb2.RecordPaymentRequest(
            loan_id=str(loan_id), transfer_reference="TRF-2", installment_number=2
        ),
        metadata=metadata,
    )

    historial = loan_stub.ListLoanPayments(
        loan_service_pb2.ListLoanPaymentsRequest(loan_id=str(loan_id)),
        metadata=metadata,
    )

    # Del más reciente al más antiguo: lo que se consulta es el último cobro.
    entrada_segundo, entrada_primero = historial.payments
    for entrada, original in (
        (entrada_primero, primero),
        (entrada_segundo, segundo),
    ):
        assert entrada.amount == original.amount_paid
        assert list(entrada.covered_installments) == list(original.covered_installments)
        assert entrada.total_paid_after == original.total_paid
        assert entrada.remaining_balance_after == original.remaining_balance
        assert entrada.transfer_reference == original.transfer_reference
        assert entrada.payment_method == original.payment_method

    # Contra lo que dijo RecordPayment, no contra un literal: lo que importa
    # es que el historial y el comprobante original cuenten las mismas cuotas.
    assert historial.total_installments == segundo.total_installments
    assert historial.total_paid == segundo.total_paid
    assert historial.remaining_balance == segundo.remaining_balance


def test_list_loan_payments_names_the_operator_who_collected(stubs):
    """El comprobante nombra al cajero, así que el historial tiene que saber
    quién cobró -- antes eso sólo estaba en el AuditLog, como texto."""
    auth_stub, loan_stub = stubs
    _create_user(
        "cashier_named",
        "Passw0rd!",
        RoleEnum.CASHIER,
        first_name="Ana",
        last_name="Giménez",
        national_id="4111222",
    )
    client_id = _create_client_row(national_id="7000043", email="hist4@example.com")
    loan_id = _create_loan_row(
        client_id, LoanStatusEnum.ACTIVE, approved_at=datetime.now(timezone.utc)
    )
    metadata = _login(auth_stub, "cashier_named", "Passw0rd!")

    loan_stub.RecordPayment(
        loan_service_pb2.RecordPaymentRequest(
            loan_id=str(loan_id), transfer_reference="TRF-QUIEN", installment_number=1
        ),
        metadata=metadata,
    )
    historial = loan_stub.ListLoanPayments(
        loan_service_pb2.ListLoanPaymentsRequest(loan_id=str(loan_id)),
        metadata=metadata,
    )

    entrada = historial.payments[0]
    assert entrada.recorded_by_name == "Ana Giménez"
    assert entrada.recorded_by_national_id == "4111222"


def test_payments_recorded_before_the_column_have_no_operator(stubs):
    """Sin backfill: un pago anterior a BR-LOAN-016 no tiene responsable, y
    atribuírselo a alguien sería inventarlo. El papel lo dice."""
    auth_stub, loan_stub = stubs
    _create_user("manager_old", "Passw0rd!", RoleEnum.MANAGER)
    client_id = _create_client_row(national_id="7000044", email="hist5@example.com")
    loan_id = _create_loan_row(
        client_id, LoanStatusEnum.ACTIVE, approved_at=datetime.now(timezone.utc)
    )
    with SessionLocal() as session:
        session.add(
            LoanPayment(
                loan_id=loan_id,
                amount=Decimal("100.00"),
                transfer_reference="TRF-VIEJO",
                paid_at=datetime.now(timezone.utc),
                recorded_by_user_id=None,
            )
        )
        session.commit()
    metadata = _login(auth_stub, "manager_old", "Passw0rd!")

    historial = loan_stub.ListLoanPayments(
        loan_service_pb2.ListLoanPaymentsRequest(loan_id=str(loan_id)),
        metadata=metadata,
    )

    entrada = historial.payments[0]
    assert entrada.recorded_by_name == ""
    assert entrada.recorded_by_national_id == ""
    # El medio también es nulo en esas filas: se lee como TRANSFERENCIA, que
    # era el único admitido antes de BR-CAJA-004.
    assert entrada.payment_method == "TRANSFERENCIA"


def test_list_loan_payments_of_an_unknown_loan_is_not_found(stubs):
    auth_stub, loan_stub = stubs
    _create_user("manager_404", "Passw0rd!", RoleEnum.MANAGER)
    metadata = _login(auth_stub, "manager_404", "Passw0rd!")

    with pytest.raises(grpc.RpcError) as exc_info:
        loan_stub.ListLoanPayments(
            loan_service_pb2.ListLoanPaymentsRequest(
                loan_id="11111111-1111-1111-1111-111111111111"
            ),
            metadata=metadata,
        )
    assert exc_info.value.code() == grpc.StatusCode.NOT_FOUND
