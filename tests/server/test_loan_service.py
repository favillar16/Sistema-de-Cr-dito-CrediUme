import threading
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import grpc
import pytest

import loan_service_pb2

from cas_server import config
from cas_server.db.base import SessionLocal
from cas_server.db.models import Client, Loan, LoanStatusEnum
from cas_server.services.amortization import calcular_cronograma
from cas_server.services.loan_service import LoanServicer

from tests.server.helpers import AbortCalled, FakeContext


def _create_client(
    national_id="9000001",
    email="loanclient@example.com",
    declared_monthly_income=Decimal("2000.00"),
    source_of_funds="Salario",
    is_active=True,
):
    with SessionLocal() as session:
        client = Client(
            first_name="Loan",
            last_name="Client",
            national_id=national_id,
            email=email,
            phone_number="0981111111",
            date_of_birth=date(1990, 1, 1),
            address="Calle Prestamo 1",
            declared_monthly_income=declared_monthly_income,
            source_of_funds=source_of_funds,
            is_active=is_active,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(client)
        session.commit()
        session.refresh(client)
        return client.id


@pytest.fixture
def servicer():
    return LoanServicer()


def _create_loan(servicer, client_id, principal="1000.00", rate="0.12", term=6):
    return servicer.CreateLoan(
        loan_service_pb2.CreateLoanRequest(
            client_id=str(client_id),
            principal_amount=principal,
            interest_rate=rate,
            term_months=term,
        ),
        FakeContext(),
    )


def test_create_loan_success_is_pending(servicer):
    client_id = _create_client()
    response = _create_loan(servicer, client_id)
    assert response.loan_id
    assert response.status == "PENDING"


def test_create_loan_missing_income_is_failed_precondition(servicer):
    client_id = _create_client(declared_monthly_income=None)

    with pytest.raises(AbortCalled) as exc_info:
        _create_loan(servicer, client_id)
    assert exc_info.value.code == grpc.StatusCode.FAILED_PRECONDITION


def test_create_loan_installment_exceeds_income_ratio_is_failed_precondition(servicer):
    client_id = _create_client(declared_monthly_income=Decimal("10.00"))

    with pytest.raises(AbortCalled) as exc_info:
        _create_loan(servicer, client_id, principal="1000.00", rate="0.24", term=12)
    assert exc_info.value.code == grpc.StatusCode.FAILED_PRECONDITION


def test_create_loan_sets_default_first_due_date(servicer):
    client_id = _create_client()
    created = _create_loan(servicer, client_id)

    response = servicer.GetLoanById(
        loan_service_pb2.GetLoanByIdRequest(loan_id=created.loan_id), FakeContext()
    )
    fecha_creacion = created.created_at.ToDatetime().date()
    fecha_esperada = fecha_creacion + timedelta(days=config.LOAN_DEFAULT_FIRST_DUE_DAYS)
    assert response.first_due_date == fecha_esperada.isoformat()


def test_update_loan_proposal_success_changes_terms(servicer):
    client_id = _create_client()
    loan = _create_loan(servicer, client_id, principal="1000.00", rate="0.12", term=6)

    response = servicer.UpdateLoanProposal(
        loan_service_pb2.UpdateLoanProposalRequest(
            loan_id=loan.loan_id,
            principal_amount="1500.00",
            term_months=9,
            first_due_date="2026-10-01",
        ),
        FakeContext(),
    )
    assert response.success
    assert response.status == "PENDING"

    fetched = servicer.GetLoanById(
        loan_service_pb2.GetLoanByIdRequest(loan_id=loan.loan_id), FakeContext()
    )
    assert fetched.principal_amount == "1500.00"
    assert fetched.term_months == 9
    assert fetched.first_due_date == "2026-10-01"


def test_update_loan_proposal_non_pending_is_failed_precondition(servicer):
    client_id = _create_client()
    loan = _create_loan(servicer, client_id)
    servicer.ApproveLoan(
        loan_service_pb2.ApproveLoanRequest(loan_id=loan.loan_id), FakeContext()
    )

    with pytest.raises(AbortCalled) as exc_info:
        servicer.UpdateLoanProposal(
            loan_service_pb2.UpdateLoanProposalRequest(
                loan_id=loan.loan_id,
                principal_amount="1500.00",
                term_months=9,
                first_due_date="2026-10-01",
            ),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.FAILED_PRECONDITION


def test_update_loan_proposal_installment_exceeds_income_ratio_is_failed_precondition(
    servicer,
):
    client_id = _create_client(declared_monthly_income=Decimal("10.00"))
    loan = _create_loan(servicer, client_id, principal="1.00", rate="0.24", term=12)

    with pytest.raises(AbortCalled) as exc_info:
        servicer.UpdateLoanProposal(
            loan_service_pb2.UpdateLoanProposalRequest(
                loan_id=loan.loan_id,
                principal_amount="1000.00",
                term_months=12,
                first_due_date="2026-10-01",
            ),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.FAILED_PRECONDITION


def test_update_loan_proposal_first_due_date_before_creation_is_invalid_argument(
    servicer,
):
    client_id = _create_client()
    loan = _create_loan(servicer, client_id)

    with pytest.raises(AbortCalled) as exc_info:
        servicer.UpdateLoanProposal(
            loan_service_pb2.UpdateLoanProposalRequest(
                loan_id=loan.loan_id,
                principal_amount="1000.00",
                term_months=6,
                first_due_date="2000-01-01",
            ),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.INVALID_ARGUMENT


def test_update_loan_proposal_not_found(servicer):
    with pytest.raises(AbortCalled) as exc_info:
        servicer.UpdateLoanProposal(
            loan_service_pb2.UpdateLoanProposalRequest(
                loan_id="00000000-0000-0000-0000-000000000000",
                principal_amount="1000.00",
                term_months=6,
                first_due_date="2026-10-01",
            ),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.NOT_FOUND


def test_update_loan_guarantee_success_set_and_clear(servicer):
    client_id = _create_client()
    loan = _create_loan(servicer, client_id)

    response = servicer.UpdateLoanGuarantee(
        loan_service_pb2.UpdateLoanGuaranteeRequest(
            loan_id=loan.loan_id,
            guarantee_type="SOLA FIRMA",
            guarantee_amount="1000.00",
        ),
        FakeContext(),
    )
    assert response.success
    fetched = servicer.GetLoanById(
        loan_service_pb2.GetLoanByIdRequest(loan_id=loan.loan_id), FakeContext()
    )
    assert fetched.guarantee_type == "SOLA FIRMA"
    assert fetched.guarantee_amount == "1000.00"

    servicer.UpdateLoanGuarantee(
        loan_service_pb2.UpdateLoanGuaranteeRequest(
            loan_id=loan.loan_id, guarantee_type="", guarantee_amount=""
        ),
        FakeContext(),
    )
    cleared = servicer.GetLoanById(
        loan_service_pb2.GetLoanByIdRequest(loan_id=loan.loan_id), FakeContext()
    )
    assert cleared.guarantee_type == ""
    assert cleared.guarantee_amount == ""


def test_update_loan_guarantee_mismatched_pair_is_invalid_argument(servicer):
    client_id = _create_client()
    loan = _create_loan(servicer, client_id)

    with pytest.raises(AbortCalled) as exc_info:
        servicer.UpdateLoanGuarantee(
            loan_service_pb2.UpdateLoanGuaranteeRequest(
                loan_id=loan.loan_id, guarantee_type="SOLA FIRMA", guarantee_amount=""
            ),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.INVALID_ARGUMENT


def test_update_loan_guarantee_non_pending_is_failed_precondition(servicer):
    client_id = _create_client()
    loan = _create_loan(servicer, client_id)
    servicer.ApproveLoan(
        loan_service_pb2.ApproveLoanRequest(loan_id=loan.loan_id), FakeContext()
    )

    with pytest.raises(AbortCalled) as exc_info:
        servicer.UpdateLoanGuarantee(
            loan_service_pb2.UpdateLoanGuaranteeRequest(
                loan_id=loan.loan_id,
                guarantee_type="SOLA FIRMA",
                guarantee_amount="1000.00",
            ),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.FAILED_PRECONDITION


def test_update_loan_guarantee_not_found(servicer):
    with pytest.raises(AbortCalled) as exc_info:
        servicer.UpdateLoanGuarantee(
            loan_service_pb2.UpdateLoanGuaranteeRequest(
                loan_id="00000000-0000-0000-0000-000000000000",
                guarantee_type="SOLA FIRMA",
                guarantee_amount="1000.00",
            ),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.NOT_FOUND


def test_update_loan_charges_success_sets_all_and_totals(servicer):
    client_id = _create_client()
    loan = _create_loan(servicer, client_id, principal="1000.00", rate="0.00", term=10)

    response = servicer.UpdateLoanCharges(
        loan_service_pb2.UpdateLoanChargesRequest(
            loan_id=loan.loan_id,
            charge_interest_tax="10.00",
            charge_admin_fee="5.00",
            charge_cancellation_insurance="3.00",
            charge_contracted_insurance="2.00",
        ),
        FakeContext(),
    )
    assert response.success
    assert response.total_charges == "20.00"

    fetched = servicer.GetLoanById(
        loan_service_pb2.GetLoanByIdRequest(loan_id=loan.loan_id), FakeContext()
    )
    assert fetched.total_charges == "20.00"
    # Sin interés (rate=0.00), el total programado (principal+interés) es 1000.00.
    assert fetched.total_credit_with_charges == "1020.00"


def test_update_loan_charges_success_with_partial_fields(servicer):
    client_id = _create_client()
    loan = _create_loan(servicer, client_id)

    response = servicer.UpdateLoanCharges(
        loan_service_pb2.UpdateLoanChargesRequest(
            loan_id=loan.loan_id, charge_admin_fee="15.00"
        ),
        FakeContext(),
    )
    assert response.total_charges == "15.00"

    fetched = servicer.GetLoanById(
        loan_service_pb2.GetLoanByIdRequest(loan_id=loan.loan_id), FakeContext()
    )
    assert fetched.charge_admin_fee == "15.00"
    assert fetched.charge_interest_tax == ""


def test_update_loan_charges_non_pending_is_failed_precondition(servicer):
    client_id = _create_client()
    loan = _create_loan(servicer, client_id)
    servicer.ApproveLoan(
        loan_service_pb2.ApproveLoanRequest(loan_id=loan.loan_id), FakeContext()
    )

    with pytest.raises(AbortCalled) as exc_info:
        servicer.UpdateLoanCharges(
            loan_service_pb2.UpdateLoanChargesRequest(
                loan_id=loan.loan_id, charge_admin_fee="15.00"
            ),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.FAILED_PRECONDITION


def test_update_loan_charges_not_found(servicer):
    with pytest.raises(AbortCalled) as exc_info:
        servicer.UpdateLoanCharges(
            loan_service_pb2.UpdateLoanChargesRequest(
                loan_id="00000000-0000-0000-0000-000000000000",
                charge_admin_fee="15.00",
            ),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.NOT_FOUND


def test_update_loan_charges_invalid_amount_is_invalid_argument(servicer):
    client_id = _create_client()
    loan = _create_loan(servicer, client_id)

    with pytest.raises(AbortCalled) as exc_info:
        servicer.UpdateLoanCharges(
            loan_service_pb2.UpdateLoanChargesRequest(
                loan_id=loan.loan_id, charge_admin_fee="not-a-number"
            ),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.INVALID_ARGUMENT


def test_charges_do_not_affect_record_payment_payoff(servicer):
    """BR-LOAN-006: los cargos son puramente informativos -- RecordPayment sigue
    basándose únicamente en capital+interés para decidir cuándo un préstamo
    pasa a PAID, sin importar los cargos cargados."""
    client_id = _create_client()
    loan = _create_loan(servicer, client_id, principal="1200.00", rate="0.00", term=12)
    servicer.UpdateLoanCharges(
        loan_service_pb2.UpdateLoanChargesRequest(
            loan_id=loan.loan_id,
            charge_interest_tax="500.00",
            charge_admin_fee="500.00",
            charge_cancellation_insurance="500.00",
            charge_contracted_insurance="500.00",
        ),
        FakeContext(),
    )
    servicer.ApproveLoan(
        loan_service_pb2.ApproveLoanRequest(loan_id=loan.loan_id), FakeContext()
    )
    servicer.DisburseLoan(
        loan_service_pb2.DisburseLoanRequest(loan_id=loan.loan_id), FakeContext()
    )

    response = servicer.RecordPayment(
        loan_service_pb2.RecordPaymentRequest(
            loan_id=loan.loan_id, amount="1200.00", transfer_reference="TRX-CHARGES"
        ),
        FakeContext(),
    )
    assert response.success
    assert response.status == "PAID"
    assert response.remaining_balance == "0.00"


def test_create_loan_blocked_at_three_active_loans(servicer):
    client_id = _create_client(declared_monthly_income=Decimal("100000.00"))

    for _ in range(3):
        loan = _create_loan(servicer, client_id)
        servicer.ApproveLoan(
            loan_service_pb2.ApproveLoanRequest(loan_id=loan.loan_id), FakeContext()
        )

    with pytest.raises(AbortCalled) as exc_info:
        _create_loan(servicer, client_id)
    assert exc_info.value.code == grpc.StatusCode.FAILED_PRECONDITION


def test_get_loan_by_id_not_found(servicer):
    with pytest.raises(AbortCalled) as exc_info:
        servicer.GetLoanById(
            loan_service_pb2.GetLoanByIdRequest(
                loan_id="00000000-0000-0000-0000-000000000000"
            ),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.NOT_FOUND


def test_get_loan_by_id_lazily_expires_overdue_approval(servicer):
    client_id = _create_client()
    loan = _create_loan(servicer, client_id)
    servicer.ApproveLoan(
        loan_service_pb2.ApproveLoanRequest(loan_id=loan.loan_id), FakeContext()
    )

    with SessionLocal() as session:
        db_loan = session.get(Loan, loan.loan_id)
        db_loan.approved_at = datetime.now(timezone.utc) - timedelta(days=31)
        session.commit()

    response = servicer.GetLoanById(
        loan_service_pb2.GetLoanByIdRequest(loan_id=loan.loan_id), FakeContext()
    )
    assert response.status == "EXPIRED"

    with SessionLocal() as session:
        db_loan = session.get(Loan, loan.loan_id)
        assert db_loan.status.value == "EXPIRED"


def test_approve_loan_success_sets_approved_at(servicer):
    client_id = _create_client()
    loan = _create_loan(servicer, client_id)

    response = servicer.ApproveLoan(
        loan_service_pb2.ApproveLoanRequest(loan_id=loan.loan_id), FakeContext()
    )
    assert response.success
    assert response.status == "APPROVED"
    assert response.approved_at.seconds > 0


def test_approve_loan_non_pending_is_failed_precondition(servicer):
    client_id = _create_client()
    loan = _create_loan(servicer, client_id)
    servicer.ApproveLoan(
        loan_service_pb2.ApproveLoanRequest(loan_id=loan.loan_id), FakeContext()
    )

    with pytest.raises(AbortCalled) as exc_info:
        servicer.ApproveLoan(
            loan_service_pb2.ApproveLoanRequest(loan_id=loan.loan_id), FakeContext()
        )
    assert exc_info.value.code == grpc.StatusCode.FAILED_PRECONDITION


def test_approve_loan_blocked_when_client_already_at_cap(servicer):
    client_id = _create_client(declared_monthly_income=Decimal("100000.00"))

    loans = [_create_loan(servicer, client_id) for _ in range(4)]
    for loan in loans[:3]:
        servicer.ApproveLoan(
            loan_service_pb2.ApproveLoanRequest(loan_id=loan.loan_id), FakeContext()
        )

    with pytest.raises(AbortCalled) as exc_info:
        servicer.ApproveLoan(
            loan_service_pb2.ApproveLoanRequest(loan_id=loans[3].loan_id), FakeContext()
        )
    assert exc_info.value.code == grpc.StatusCode.FAILED_PRECONDITION


def test_approve_loan_concurrent_requests_respect_active_loan_cap(servicer):
    """ES-006 §3.1 / BR-LOAN-001: a client at 2 active loans (cap is 3) has
    two more PENDING loans approved concurrently. Without locking the
    Client row (with_for_update), both ApproveLoan calls can independently
    count 2 active loans and both pass, pushing the client to 4 -- over the
    cap. Runs two real threads against the real DB, unlike the sequential
    test above (which only proves the cap works when requests are
    sequential)."""
    client_id = _create_client(declared_monthly_income=Decimal("100000.00"))
    _activate_loan(servicer, client_id)
    _activate_loan(servicer, client_id)
    pending = [_create_loan(servicer, client_id) for _ in range(2)]

    results = []
    errors = []
    barrier = threading.Barrier(2)

    def worker(loan_id: str) -> None:
        barrier.wait()
        try:
            response = servicer.ApproveLoan(
                loan_service_pb2.ApproveLoanRequest(loan_id=loan_id), FakeContext()
            )
            results.append(response)
        except AbortCalled as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(loan.loan_id,)) for loan in pending
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 1
    assert len(errors) == 1
    assert errors[0].code == grpc.StatusCode.FAILED_PRECONDITION

    with SessionLocal() as session:
        active_count = (
            session.query(Loan)
            .filter(
                Loan.client_id == client_id,
                Loan.status.in_([LoanStatusEnum.APPROVED, LoanStatusEnum.ACTIVE]),
            )
            .count()
        )
    assert active_count == config.LOAN_MAX_ACTIVE_PER_CLIENT


def test_disburse_loan_success_transitions_to_active(servicer):
    client_id = _create_client()
    loan = _create_loan(servicer, client_id)
    servicer.ApproveLoan(
        loan_service_pb2.ApproveLoanRequest(loan_id=loan.loan_id), FakeContext()
    )

    response = servicer.DisburseLoan(
        loan_service_pb2.DisburseLoanRequest(loan_id=loan.loan_id), FakeContext()
    )
    assert response.success
    assert response.status == "ACTIVE"


def test_disburse_loan_rejects_expired_approval(servicer):
    client_id = _create_client()
    loan = _create_loan(servicer, client_id)
    servicer.ApproveLoan(
        loan_service_pb2.ApproveLoanRequest(loan_id=loan.loan_id), FakeContext()
    )

    with SessionLocal() as session:
        db_loan = session.get(Loan, loan.loan_id)
        db_loan.approved_at = datetime.now(timezone.utc) - timedelta(days=31)
        session.commit()

    with pytest.raises(AbortCalled) as exc_info:
        servicer.DisburseLoan(
            loan_service_pb2.DisburseLoanRequest(loan_id=loan.loan_id), FakeContext()
        )
    assert exc_info.value.code == grpc.StatusCode.FAILED_PRECONDITION

    with SessionLocal() as session:
        db_loan = session.get(Loan, loan.loan_id)
        assert db_loan.status.value == "EXPIRED"


def _activate_loan(servicer, client_id, principal="1200.00", rate="0.00", term=12):
    loan = _create_loan(servicer, client_id, principal=principal, rate=rate, term=term)
    servicer.ApproveLoan(
        loan_service_pb2.ApproveLoanRequest(loan_id=loan.loan_id), FakeContext()
    )
    servicer.DisburseLoan(
        loan_service_pb2.DisburseLoanRequest(loan_id=loan.loan_id), FakeContext()
    )
    return loan.loan_id


def test_record_payment_partial_keeps_active(servicer):
    client_id = _create_client()
    loan_id = _activate_loan(servicer, client_id)

    response = servicer.RecordPayment(
        loan_service_pb2.RecordPaymentRequest(
            loan_id=loan_id, amount="100.00", transfer_reference="TRX-001"
        ),
        FakeContext(),
    )
    assert response.success
    assert response.status == "ACTIVE"
    assert response.total_paid == "100.00"
    assert response.remaining_balance == "1100.00"


def test_record_payment_full_transitions_to_paid(servicer):
    client_id = _create_client()
    loan_id = _activate_loan(
        servicer, client_id, principal="1200.00", rate="0.00", term=12
    )
    total = sum(
        (
            fila.monto_cuota
            for fila in calcular_cronograma(Decimal("1200.00"), Decimal("0.00"), 12)
        ),
        Decimal("0.00"),
    )

    response = servicer.RecordPayment(
        loan_service_pb2.RecordPaymentRequest(
            loan_id=loan_id, amount=str(total), transfer_reference="TRX-002"
        ),
        FakeContext(),
    )
    assert response.success
    assert response.status == "PAID"
    assert response.remaining_balance == "0.00"


def test_record_payment_concurrent_full_payoff_transitions_to_paid(servicer):
    """ES-006 §3.1: two concurrent partial payments that together cover the
    loan must still flip status to PAID. Without locking the Loan row
    (with_for_update), each payment can independently read a total_paid
    that, combined with just its own amount, doesn't reach total_programado
    -- leaving the loan stuck ACTIVE despite being fully paid. Runs two
    real threads against the real DB, unlike the sequential test above."""
    client_id = _create_client()
    loan_id = _activate_loan(
        servicer, client_id, principal="1200.00", rate="0.00", term=12
    )

    results = []
    errors = []
    barrier = threading.Barrier(2)

    def worker(amount: str, reference: str) -> None:
        barrier.wait()
        try:
            response = servicer.RecordPayment(
                loan_service_pb2.RecordPaymentRequest(
                    loan_id=loan_id, amount=amount, transfer_reference=reference
                ),
                FakeContext(),
            )
            results.append(response)
        except AbortCalled as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=("600.00", "TRX-A")),
        threading.Thread(target=worker, args=("600.00", "TRX-B")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(errors) == 0
    assert len(results) == 2

    fetched = servicer.GetLoanById(
        loan_service_pb2.GetLoanByIdRequest(loan_id=loan_id), FakeContext()
    )
    assert fetched.status == "PAID"
    assert fetched.total_paid == "1200.00"
    assert fetched.remaining_balance == "0.00"


def test_record_payment_rejects_non_active_loan(servicer):
    client_id = _create_client()
    loan = _create_loan(servicer, client_id)

    with pytest.raises(AbortCalled) as exc_info:
        servicer.RecordPayment(
            loan_service_pb2.RecordPaymentRequest(
                loan_id=loan.loan_id, amount="10.00", transfer_reference="TRX-003"
            ),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.FAILED_PRECONDITION


def test_record_payment_missing_transfer_reference_is_invalid_argument(servicer):
    client_id = _create_client()
    loan_id = _activate_loan(servicer, client_id)

    with pytest.raises(AbortCalled) as exc_info:
        servicer.RecordPayment(
            loan_service_pb2.RecordPaymentRequest(loan_id=loan_id, amount="10.00"),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.INVALID_ARGUMENT


def test_mark_defaulted_success(servicer):
    client_id = _create_client()
    loan_id = _activate_loan(servicer, client_id)

    response = servicer.MarkDefaulted(
        loan_service_pb2.MarkDefaultedRequest(loan_id=loan_id), FakeContext()
    )
    assert response.success
    assert response.status == "DEFAULTED"


def test_mark_defaulted_rejects_non_active_loan(servicer):
    client_id = _create_client()
    loan = _create_loan(servicer, client_id)

    with pytest.raises(AbortCalled) as exc_info:
        servicer.MarkDefaulted(
            loan_service_pb2.MarkDefaultedRequest(loan_id=loan.loan_id), FakeContext()
        )
    assert exc_info.value.code == grpc.StatusCode.FAILED_PRECONDITION


def test_get_amortization_schedule_row_count_and_totals(servicer):
    client_id = _create_client()
    loan = _create_loan(servicer, client_id, principal="1200.00", rate="0.12", term=12)

    response = servicer.GetAmortizationSchedule(
        loan_service_pb2.GetAmortizationScheduleRequest(loan_id=loan.loan_id),
        FakeContext(),
    )
    assert len(response.installments) == 12
    assert response.installments[-1].remaining_balance == "0.00"
    assert response.total_paid == "0.00"


def test_update_installment_amount_success_reflects_in_schedule_and_totals(servicer):
    client_id = _create_client()
    loan_id = _activate_loan(
        servicer, client_id, principal="1200.00", rate="0.12", term=12
    )

    response = servicer.UpdateInstallmentAmount(
        loan_service_pb2.UpdateInstallmentAmountRequest(
            loan_id=loan_id, installment_number=2, adjusted_amount="200.00"
        ),
        FakeContext(),
    )
    assert response.success
    assert response.status == "ACTIVE"

    schedule = servicer.GetAmortizationSchedule(
        loan_service_pb2.GetAmortizationScheduleRequest(loan_id=loan_id), FakeContext()
    )
    assert schedule.installments[1].installment_number == 2
    assert schedule.installments[1].payment_amount == "200.00"
    assert schedule.installments[1].is_adjusted is True
    assert schedule.installments[0].is_adjusted is False

    total_programado = sum(
        (Decimal(fila.payment_amount) for fila in schedule.installments),
        Decimal("0.00"),
    )
    fetched = servicer.GetLoanById(
        loan_service_pb2.GetLoanByIdRequest(loan_id=loan_id), FakeContext()
    )
    assert (
        Decimal(fetched.total_paid) + Decimal(fetched.remaining_balance)
        == total_programado
    )


def test_update_installment_amount_upsert_replaces_previous_value(servicer):
    client_id = _create_client()
    loan_id = _activate_loan(
        servicer, client_id, principal="1200.00", rate="0.12", term=12
    )

    servicer.UpdateInstallmentAmount(
        loan_service_pb2.UpdateInstallmentAmountRequest(
            loan_id=loan_id, installment_number=2, adjusted_amount="150.00"
        ),
        FakeContext(),
    )
    servicer.UpdateInstallmentAmount(
        loan_service_pb2.UpdateInstallmentAmountRequest(
            loan_id=loan_id, installment_number=2, adjusted_amount="200.00"
        ),
        FakeContext(),
    )

    schedule = servicer.GetAmortizationSchedule(
        loan_service_pb2.GetAmortizationScheduleRequest(loan_id=loan_id), FakeContext()
    )
    assert schedule.installments[1].payment_amount == "200.00"

    with SessionLocal() as session:
        from cas_server.db.models import LoanInstallmentAdjustment

        count = (
            session.query(LoanInstallmentAdjustment)
            .filter(LoanInstallmentAdjustment.loan_id == loan_id)
            .count()
        )
        assert count == 1


def test_update_installment_amount_rejects_non_active_loan(servicer):
    client_id = _create_client()
    loan = _create_loan(servicer, client_id)

    with pytest.raises(AbortCalled) as exc_info:
        servicer.UpdateInstallmentAmount(
            loan_service_pb2.UpdateInstallmentAmountRequest(
                loan_id=loan.loan_id, installment_number=1, adjusted_amount="50.00"
            ),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.FAILED_PRECONDITION


def test_update_installment_amount_rejects_last_installment(servicer):
    client_id = _create_client()
    loan_id = _activate_loan(
        servicer, client_id, principal="1200.00", rate="0.12", term=12
    )

    with pytest.raises(AbortCalled) as exc_info:
        servicer.UpdateInstallmentAmount(
            loan_service_pb2.UpdateInstallmentAmountRequest(
                loan_id=loan_id, installment_number=12, adjusted_amount="200.00"
            ),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.INVALID_ARGUMENT


def test_update_installment_amount_rejects_installment_number_below_one(servicer):
    client_id = _create_client()
    loan_id = _activate_loan(servicer, client_id)

    with pytest.raises(AbortCalled) as exc_info:
        servicer.UpdateInstallmentAmount(
            loan_service_pb2.UpdateInstallmentAmountRequest(
                loan_id=loan_id, installment_number=0, adjusted_amount="50.00"
            ),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.INVALID_ARGUMENT


def test_update_installment_amount_rejects_amount_below_interest(servicer):
    client_id = _create_client()
    loan_id = _activate_loan(
        servicer, client_id, principal="1200.00", rate="0.12", term=12
    )

    with pytest.raises(AbortCalled) as exc_info:
        servicer.UpdateInstallmentAmount(
            loan_service_pb2.UpdateInstallmentAmountRequest(
                loan_id=loan_id, installment_number=1, adjusted_amount="0.01"
            ),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.INVALID_ARGUMENT


def test_update_installment_amount_rejects_amount_overshooting_balance(servicer):
    client_id = _create_client()
    loan_id = _activate_loan(
        servicer, client_id, principal="1200.00", rate="0.12", term=12
    )

    with pytest.raises(AbortCalled) as exc_info:
        servicer.UpdateInstallmentAmount(
            loan_service_pb2.UpdateInstallmentAmountRequest(
                loan_id=loan_id, installment_number=1, adjusted_amount="5000.00"
            ),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.INVALID_ARGUMENT


def test_update_installment_amount_not_found(servicer):
    with pytest.raises(AbortCalled) as exc_info:
        servicer.UpdateInstallmentAmount(
            loan_service_pb2.UpdateInstallmentAmountRequest(
                loan_id="00000000-0000-0000-0000-000000000000",
                installment_number=1,
                adjusted_amount="50.00",
            ),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.NOT_FOUND


def test_record_payment_respects_adjusted_total_programado(servicer):
    """El ajuste de una cuota cambia el total programado que RecordPayment usa
    (vía _totales_prestamo) para decidir la transición a PAID."""
    client_id = _create_client()
    loan_id = _activate_loan(
        servicer, client_id, principal="1200.00", rate="0.12", term=12
    )

    servicer.UpdateInstallmentAmount(
        loan_service_pb2.UpdateInstallmentAmountRequest(
            loan_id=loan_id, installment_number=1, adjusted_amount="150.00"
        ),
        FakeContext(),
    )

    cronograma_ajustado = calcular_cronograma(
        Decimal("1200.00"), Decimal("0.12"), 12, ajustes={1: Decimal("150.00")}
    )
    total_esperado = sum(
        (fila.monto_cuota for fila in cronograma_ajustado), Decimal("0.00")
    )
    assert total_esperado != Decimal("1200.00")  # confirma que el ajuste mueve el total

    response = servicer.RecordPayment(
        loan_service_pb2.RecordPaymentRequest(
            loan_id=loan_id, amount=str(total_esperado), transfer_reference="TRX-ADJ"
        ),
        FakeContext(),
    )
    assert response.success
    assert response.status == "PAID"
    assert response.remaining_balance == "0.00"


# ---- BR-LOAN-011: comprobante de pago -------------------------------------


def _activar_prestamo(servicer, client_id, principal="1200.00", rate="0.00", term=12):
    """Préstamo ACTIVE listo para recibir pagos. rate="0.00" por defecto para
    que cada cuota sea un número redondo (1200/12 = 100.00) y los asserts de
    cuotas cubiertas no dependan de la aritmética del sistema francés."""
    loan = _create_loan(servicer, client_id, principal=principal, rate=rate, term=term)
    servicer.ApproveLoan(
        loan_service_pb2.ApproveLoanRequest(loan_id=loan.loan_id), FakeContext()
    )
    servicer.DisburseLoan(
        loan_service_pb2.DisburseLoanRequest(loan_id=loan.loan_id), FakeContext()
    )
    return loan


def test_record_payment_reports_the_single_installment_it_covered(servicer):
    """El caso "Cuota(s) 1 de 18" del comprobante."""
    client_id = _create_client()
    loan = _activar_prestamo(servicer, client_id)

    response = servicer.RecordPayment(
        loan_service_pb2.RecordPaymentRequest(
            loan_id=loan.loan_id, transfer_reference="TRF-1", installment_number=1
        ),
        FakeContext(),
    )
    assert list(response.covered_installments) == [1]
    assert response.total_installments == 12
    assert Decimal(response.amount_paid) == Decimal("100.00")
    assert response.transfer_reference == "TRF-1"


def test_record_payment_reports_every_installment_a_large_payment_spans(servicer):
    """El caso "Cuota(s) 1,2 de 18": un solo pago que cubre más de una cuota."""
    client_id = _create_client()
    loan = _activar_prestamo(servicer, client_id)

    response = servicer.RecordPayment(
        loan_service_pb2.RecordPaymentRequest(
            loan_id=loan.loan_id, amount="200.00", transfer_reference="TRF-2"
        ),
        FakeContext(),
    )
    assert list(response.covered_installments) == [1, 2]


def test_record_payment_covered_installments_advance_with_previous_payments(servicer):
    """El segundo pago informa la cuota 2, no vuelve a informar la 1 -- la
    imputación arranca donde quedó el acumulado anterior."""
    client_id = _create_client()
    loan = _activar_prestamo(servicer, client_id)

    servicer.RecordPayment(
        loan_service_pb2.RecordPaymentRequest(
            loan_id=loan.loan_id, transfer_reference="TRF-1", installment_number=1
        ),
        FakeContext(),
    )
    segundo = servicer.RecordPayment(
        loan_service_pb2.RecordPaymentRequest(
            loan_id=loan.loan_id, transfer_reference="TRF-2", installment_number=2
        ),
        FakeContext(),
    )
    assert list(segundo.covered_installments) == [2]


def test_record_payment_reports_the_partially_covered_installment(servicer):
    """Un pago que no alcanza a saldar una cuota igual informa a qué cuota se
    imputó -- el comprobante dice dónde se aplicó el dinero, no qué quedó
    saldado."""
    client_id = _create_client()
    loan = _activar_prestamo(servicer, client_id)

    response = servicer.RecordPayment(
        loan_service_pb2.RecordPaymentRequest(
            loan_id=loan.loan_id, amount="40.00", transfer_reference="TRF-3"
        ),
        FakeContext(),
    )
    assert list(response.covered_installments) == [1]
    assert response.status == "ACTIVE"


def test_record_payment_covered_installments_match_the_schedule_fifo(servicer):
    """Guarda de coherencia: lo que informa el comprobante tiene que ser lo
    mismo que el cronograma marca como pagado justo después. Si esto se
    desincroniza, el papel que recibe el cliente contradice la pantalla."""
    client_id = _create_client()
    loan = _activar_prestamo(servicer, client_id)

    response = servicer.RecordPayment(
        loan_service_pb2.RecordPaymentRequest(
            loan_id=loan.loan_id, amount="250.00", transfer_reference="TRF-4"
        ),
        FakeContext(),
    )
    schedule = servicer.GetAmortizationSchedule(
        loan_service_pb2.GetAmortizationScheduleRequest(loan_id=loan.loan_id),
        FakeContext(),
    )
    pagadas = [i.installment_number for i in schedule.installments if i.is_paid]
    # 250 cubre las cuotas 1 y 2 enteras y parte de la 3.
    assert pagadas == [1, 2]
    assert list(response.covered_installments) == [1, 2, 3]


def test_record_payment_final_payment_reports_paid_status(servicer):
    client_id = _create_client()
    loan = _activar_prestamo(servicer, client_id, principal="100.00", term=1)

    response = servicer.RecordPayment(
        loan_service_pb2.RecordPaymentRequest(
            loan_id=loan.loan_id, transfer_reference="TRF-5", installment_number=1
        ),
        FakeContext(),
    )
    assert response.status == "PAID"
    assert list(response.covered_installments) == [1]
    assert Decimal(response.remaining_balance) == Decimal("0.00")


def test_record_payment_without_credentials_leaves_operator_fields_empty(servicer):
    """Los tests que llaman al servicer directo no pasan por AuthInterceptor,
    así que no hay claims -- mismo patrón tolerante a None que id_actor_actual.
    La cobertura real del "Registrado por" está en
    test_loan_interceptor_integration.py."""
    client_id = _create_client()
    loan = _activar_prestamo(servicer, client_id)

    response = servicer.RecordPayment(
        loan_service_pb2.RecordPaymentRequest(
            loan_id=loan.loan_id, transfer_reference="TRF-6", installment_number=1
        ),
        FakeContext(),
    )
    assert response.recorded_by_name == ""
    assert response.recorded_by_national_id == ""


def test_update_loan_proposal_reports_a_client_without_declared_income(servicer):
    """Antes de la verificación explícita esto reventaba con `None * Decimal`
    al calcular el tope del 40%, y la RPC contestaba UNKNOWN -- que la vista
    traduce a "Ocurrió un error inesperado", sin decirle al operador que lo
    que falta es el ingreso declarado del cliente.

    El ingreso se borra después de crear el préstamo porque CreateLoan ya
    exige tenerlo: la propuesta sobrevive a que la ficha del cliente quede
    incompleta, y es ahí donde la validación faltaba.
    """
    client_id = _create_client()
    loan = _create_loan(servicer, client_id)
    with SessionLocal() as session:
        session.get(Client, client_id).declared_monthly_income = None
        session.commit()

    with pytest.raises(AbortCalled) as exc_info:
        servicer.UpdateLoanProposal(
            loan_service_pb2.UpdateLoanProposalRequest(
                loan_id=loan.loan_id,
                principal_amount="1500.00",
                term_months=9,
                first_due_date="2026-10-01",
            ),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.FAILED_PRECONDITION
    assert "ingresos declarados" in exc_info.value.message
