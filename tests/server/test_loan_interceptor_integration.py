"""RBAC coverage for LoanService, mirroring test_interceptor_integration.py's
pattern: a real grpc.Server with AuthInterceptor wired in, driven over an
actual channel so metadata-based auth is exercised for real."""

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
from cas_server.db.models import Client, Loan, LoanStatusEnum, RoleEnum, User
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


def test_create_loan_allows_cashier(stubs):
    auth_stub, loan_stub = stubs
    _create_user("cashier_l", "Passw0rd!", RoleEnum.CASHIER)
    client_id = _create_client_row(
        national_id="7000002", email="cashier_loan@example.com"
    )
    metadata = _login(auth_stub, "cashier_l", "Passw0rd!")

    response = loan_stub.CreateLoan(
        loan_service_pb2.CreateLoanRequest(
            client_id=str(client_id),
            principal_amount="1000.00",
            interest_rate="0.24",
            term_months=6,
        ),
        metadata=metadata,
    )
    assert response.loan_id


def test_create_loan_rejects_non_standard_rate_for_standard_role(stubs):
    auth_stub, loan_stub = stubs
    _create_user("cashier_r", "Passw0rd!", RoleEnum.CASHIER)
    client_id = _create_client_row(national_id="7000008", email="rate_std@example.com")
    metadata = _login(auth_stub, "cashier_r", "Passw0rd!")

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


def test_create_loan_allows_manager_to_set_custom_rate(stubs):
    auth_stub, loan_stub = stubs
    _create_user("manager_r", "Passw0rd!", RoleEnum.MANAGER)
    client_id = _create_client_row(
        national_id="7000009", email="rate_manager@example.com"
    )
    metadata = _login(auth_stub, "manager_r", "Passw0rd!")

    response = loan_stub.CreateLoan(
        loan_service_pb2.CreateLoanRequest(
            client_id=str(client_id),
            principal_amount="1000.00",
            interest_rate="0.10",
            term_months=6,
        ),
        metadata=metadata,
    )
    assert response.loan_id


def test_update_loan_proposal_allows_cashier(stubs):
    auth_stub, loan_stub = stubs
    _create_user("cashier_u", "Passw0rd!", RoleEnum.CASHIER)
    client_id = _create_client_row(
        national_id="7000005", email="update_proposal@example.com"
    )
    loan_id = _create_loan_row(client_id, LoanStatusEnum.PENDING)
    metadata = _login(auth_stub, "cashier_u", "Passw0rd!")

    response = loan_stub.UpdateLoanProposal(
        loan_service_pb2.UpdateLoanProposalRequest(
            loan_id=str(loan_id),
            principal_amount="1200.00",
            term_months=8,
            first_due_date="2026-09-01",
        ),
        metadata=metadata,
    )
    assert response.success
    assert response.status == "PENDING"


def test_update_loan_guarantee_allows_cashier(stubs):
    auth_stub, loan_stub = stubs
    _create_user("cashier_g", "Passw0rd!", RoleEnum.CASHIER)
    client_id = _create_client_row(
        national_id="7000006", email="update_guarantee@example.com"
    )
    loan_id = _create_loan_row(client_id, LoanStatusEnum.PENDING)
    metadata = _login(auth_stub, "cashier_g", "Passw0rd!")

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


def test_update_loan_charges_allows_cashier(stubs):
    auth_stub, loan_stub = stubs
    _create_user("cashier_c", "Passw0rd!", RoleEnum.CASHIER)
    client_id = _create_client_row(
        national_id="7000007", email="update_charges@example.com"
    )
    loan_id = _create_loan_row(client_id, LoanStatusEnum.PENDING)
    metadata = _login(auth_stub, "cashier_c", "Passw0rd!")

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


def test_get_loan_by_id_reports_creating_advisor(stubs):
    """created_by_username is set from whoever authenticated CreateLoan --
    used by the client to print an advisor name on the cronograma de pago
    handed to the client (see documents.py's cronograma_html)."""
    auth_stub, loan_stub = stubs
    _create_user("cashier_adv", "Passw0rd!", RoleEnum.CASHIER)
    client_id = _create_client_row(national_id="7000011", email="advisor@example.com")
    metadata = _login(auth_stub, "cashier_adv", "Passw0rd!")

    created = loan_stub.CreateLoan(
        loan_service_pb2.CreateLoanRequest(
            client_id=str(client_id),
            principal_amount="1000.00",
            interest_rate="0.24",
            term_months=6,
        ),
        metadata=metadata,
    )

    detail = loan_stub.GetLoanById(
        loan_service_pb2.GetLoanByIdRequest(loan_id=created.loan_id),
        metadata=metadata,
    )
    assert detail.created_by_username == "cashier_adv"


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
        "cashier_named",
        "Passw0rd!",
        RoleEnum.CASHIER,
        first_name="Ana",
        last_name="Benítez",
        national_id="4123456",
    )
    client_id = _create_client_row(national_id="7000020", email="named@example.com")
    metadata = _login(auth_stub, "cashier_named", "Passw0rd!")

    created = loan_stub.CreateLoan(
        loan_service_pb2.CreateLoanRequest(
            client_id=str(client_id),
            principal_amount="1000.00",
            interest_rate="0.24",
            term_months=6,
        ),
        metadata=metadata,
    )
    detail = loan_stub.GetLoanById(
        loan_service_pb2.GetLoanByIdRequest(loan_id=created.loan_id), metadata=metadata
    )
    assert detail.created_by_full_name == "Ana Benítez"
    assert detail.created_by_national_id == "4123456"
    assert detail.created_by_username == "cashier_named"


def test_get_loan_by_id_advisor_personal_data_empty_for_legacy_user(stubs):
    """Un operador sin datos personales cargados devuelve "" en los campos
    nuevos -- el documento cae de vuelta a created_by_username."""
    auth_stub, loan_stub = stubs
    _create_user("cashier_unnamed", "Passw0rd!", RoleEnum.CASHIER)
    client_id = _create_client_row(national_id="7000021", email="unnamed@example.com")
    metadata = _login(auth_stub, "cashier_unnamed", "Passw0rd!")

    created = loan_stub.CreateLoan(
        loan_service_pb2.CreateLoanRequest(
            client_id=str(client_id),
            principal_amount="1000.00",
            interest_rate="0.24",
            term_months=6,
        ),
        metadata=metadata,
    )
    detail = loan_stub.GetLoanById(
        loan_service_pb2.GetLoanByIdRequest(loan_id=created.loan_id), metadata=metadata
    )
    assert detail.created_by_full_name == ""
    assert detail.created_by_national_id == ""
    assert detail.created_by_username == "cashier_unnamed"


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
