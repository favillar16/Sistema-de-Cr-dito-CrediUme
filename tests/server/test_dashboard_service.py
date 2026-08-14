from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import dashboard_service_pb2
import grpc
import pytest

from cas_server.db.base import SessionLocal
from cas_server.db.models import Client, Loan, LoanPayment, LoanStatusEnum
from cas_server.services.amortization import calcular_cronograma
from cas_server.services.dashboard_service import DashboardServicer

from tests.server.helpers import AbortCalled, FakeContext


@pytest.fixture
def servicer():
    return DashboardServicer()


def _create_client(national_id, email, *, is_active=True):
    with SessionLocal() as session:
        client = Client(
            first_name="Dash",
            last_name="Client",
            national_id=national_id,
            email=email,
            phone_number="0981111111",
            date_of_birth=date(1990, 1, 1),
            address="Calle Dashboard 1",
            declared_monthly_income=Decimal("2000.00"),
            is_active=is_active,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(client)
        session.commit()
        session.refresh(client)
        return client.id


def _create_loan(
    client_id,
    status,
    *,
    principal="1000.00",
    rate="0.12",
    term=6,
    approved_at=None,
    first_due_date=None,
    created_at=None,
):
    with SessionLocal() as session:
        loan = Loan(
            client_id=client_id,
            principal_amount=Decimal(principal),
            interest_rate=Decimal(rate),
            term_months=term,
            first_due_date=first_due_date or datetime.now(timezone.utc).date(),
            status=status,
            created_at=created_at or datetime.now(timezone.utc),
            approved_at=approved_at,
        )
        session.add(loan)
        session.commit()
        session.refresh(loan)
        return loan.id


def _record_payment(loan_id, amount, *, paid_at=None):
    with SessionLocal() as session:
        session.add(
            LoanPayment(
                loan_id=loan_id,
                amount=Decimal(amount),
                transfer_reference="TRF-TEST",
                paid_at=paid_at or datetime.now(timezone.utc),
            )
        )
        session.commit()


def _cuota(principal="1000.00", rate="0.12", term=6):
    """Monto de una cuota del cronograma francés, para no hardcodear en los
    asserts el resultado de la misma matemática que el servidor usa."""
    return calcular_cronograma(Decimal(principal), Decimal(rate), term)[0].monto_cuota


def test_empty_database_returns_zeroed_stats(servicer):
    response = servicer.GetDashboardStats(
        dashboard_service_pb2.GetDashboardStatsRequest(), FakeContext()
    )
    assert response.total_clients_count == 0
    assert response.active_clients_count == 0
    assert response.pending_loans_count == 0
    assert response.total_disbursed == "0.00"
    assert response.total_outstanding_balance == "0.00"


def test_counts_clients_and_loans_by_status(servicer):
    active_client = _create_client("8000001", "dash_active@example.com")
    _create_client("8000002", "dash_inactive@example.com", is_active=False)

    _create_loan(active_client, LoanStatusEnum.PENDING)
    _create_loan(
        active_client, LoanStatusEnum.APPROVED, approved_at=datetime.now(timezone.utc)
    )
    _create_loan(active_client, LoanStatusEnum.ACTIVE)
    _create_loan(active_client, LoanStatusEnum.PAID)
    _create_loan(active_client, LoanStatusEnum.DEFAULTED)

    response = servicer.GetDashboardStats(
        dashboard_service_pb2.GetDashboardStatsRequest(), FakeContext()
    )
    assert response.total_clients_count == 2
    assert response.active_clients_count == 1
    assert response.pending_loans_count == 1
    assert response.approved_loans_count == 1
    assert response.active_loans_count == 1
    assert response.paid_loans_count == 1
    assert response.defaulted_loans_count == 1
    # ACTIVE + PAID + DEFAULTED, 1000.00 each
    assert Decimal(response.total_disbursed) == Decimal("3000.00")


def test_overdue_total_sums_only_unpaid_past_due_installments(servicer):
    """BR-DASH-001: la mora es lo vencido e impago, no el saldo entero."""
    client_id = _create_client("8000010", "dash_overdue@example.com")
    # Primer vencimiento hace 40 días => vencieron la cuota 1 (hace 40 días)
    # y la 2 (un mes después, ~10 días atrás); la 3 recién vence en ~20 días.
    primer_vencimiento = datetime.now(timezone.utc).date() - timedelta(days=40)
    _create_loan(client_id, LoanStatusEnum.ACTIVE, first_due_date=primer_vencimiento)

    response = servicer.GetDashboardStats(
        dashboard_service_pb2.GetDashboardStatsRequest(), FakeContext()
    )
    assert response.overdue_loans_count == 1
    assert Decimal(response.total_overdue_amount) == _cuota() * 2
    # La mora es estrictamente menor que el saldo total: el saldo incluye
    # además las 4 cuotas que todavía no vencieron.
    assert Decimal(response.total_overdue_amount) < Decimal(
        response.total_outstanding_balance
    )


def test_overdue_total_is_zero_for_a_loan_paid_up_to_date(servicer):
    client_id = _create_client("8000011", "dash_up_to_date@example.com")
    primer_vencimiento = datetime.now(timezone.utc).date() - timedelta(days=40)
    loan_id = _create_loan(
        client_id, LoanStatusEnum.ACTIVE, first_due_date=primer_vencimiento
    )
    _record_payment(loan_id, str(_cuota() * 2))

    response = servicer.GetDashboardStats(
        dashboard_service_pb2.GetDashboardStatsRequest(), FakeContext()
    )
    assert response.overdue_loans_count == 0
    assert Decimal(response.total_overdue_amount) == Decimal("0.00")
    # ...pero el préstamo sigue teniendo saldo: mora y saldo son cosas distintas.
    assert Decimal(response.total_outstanding_balance) > Decimal("0.00")


def test_period_report_counts_only_events_inside_the_range(servicer):
    """BR-DASH-002: lo de "movimiento del período" se filtra por fecha; la
    "situación al cierre" no."""
    client_id = _create_client("8000012", "dash_report@example.com")
    hoy = datetime.now(timezone.utc)
    dentro = hoy - timedelta(days=3)
    fuera = hoy - timedelta(days=90)

    _create_loan(
        client_id,
        LoanStatusEnum.ACTIVE,
        created_at=dentro,
        approved_at=dentro,
        principal="1000.00",
    )
    _create_loan(
        client_id,
        LoanStatusEnum.ACTIVE,
        created_at=fuera,
        approved_at=fuera,
        principal="5000.00",
    )

    inicio = (hoy - timedelta(days=7)).date()
    response = servicer.GetPeriodReport(
        dashboard_service_pb2.GetPeriodReportRequest(
            start_date=inicio.isoformat(), end_date=hoy.date().isoformat()
        ),
        FakeContext(),
    )

    assert response.start_date == inicio.isoformat()
    assert response.loans_created == 1
    assert response.loans_approved == 1
    assert Decimal(response.principal_created) == Decimal("1000.00")
    assert Decimal(response.principal_approved) == Decimal("1000.00")
    # La foto al cierre ignora el rango: ambos préstamos siguen ACTIVE hoy.
    assert response.active_loans_at_close == 2


def test_period_report_totals_payments_received_in_range(servicer):
    client_id = _create_client("8000013", "dash_report_pay@example.com")
    loan_id = _create_loan(client_id, LoanStatusEnum.ACTIVE)
    hoy = datetime.now(timezone.utc)
    _record_payment(loan_id, "100.00", paid_at=hoy - timedelta(days=2))
    _record_payment(loan_id, "250.00", paid_at=hoy - timedelta(days=1))
    _record_payment(loan_id, "999.00", paid_at=hoy - timedelta(days=60))

    response = servicer.GetPeriodReport(
        dashboard_service_pb2.GetPeriodReportRequest(
            start_date=(hoy - timedelta(days=7)).date().isoformat(),
            end_date=hoy.date().isoformat(),
        ),
        FakeContext(),
    )
    assert response.payments_count == 2
    assert Decimal(response.payments_total) == Decimal("350.00")


def test_period_report_includes_the_whole_final_day(servicer):
    """El rango es inclusivo: un pago de hoy tiene que entrar en un reporte
    cuyo end_date es hoy, aunque la hora del pago sea posterior a 00:00."""
    client_id = _create_client("8000014", "dash_report_edge@example.com")
    loan_id = _create_loan(client_id, LoanStatusEnum.ACTIVE)
    hoy = datetime.now(timezone.utc)
    _record_payment(loan_id, "77.00", paid_at=hoy)

    response = servicer.GetPeriodReport(
        dashboard_service_pb2.GetPeriodReportRequest(
            start_date=hoy.date().isoformat(), end_date=hoy.date().isoformat()
        ),
        FakeContext(),
    )
    assert response.payments_count == 1
    assert Decimal(response.payments_total) == Decimal("77.00")


def test_period_report_rejects_an_inverted_range(servicer):
    with pytest.raises(AbortCalled) as excinfo:
        servicer.GetPeriodReport(
            dashboard_service_pb2.GetPeriodReportRequest(
                start_date="2026-05-10", end_date="2026-05-01"
            ),
            FakeContext(),
        )
    assert excinfo.value.code == grpc.StatusCode.INVALID_ARGUMENT


def test_period_report_rejects_a_malformed_date(servicer):
    """El formato de cable sigue siendo ISO -- que la UI muestre DD/MM/AAAA no
    cambia el contrato (formatting.fecha_a_iso traduce antes de llamar)."""
    with pytest.raises(AbortCalled) as excinfo:
        servicer.GetPeriodReport(
            dashboard_service_pb2.GetPeriodReportRequest(
                start_date="10/05/2026", end_date="2026-05-20"
            ),
            FakeContext(),
        )
    assert excinfo.value.code == grpc.StatusCode.INVALID_ARGUMENT


def test_expires_stale_approved_loans_lazily_like_other_reads(servicer):
    client_id = _create_client("8000003", "dash_expiry@example.com")
    stale_approved_at = datetime.now(timezone.utc) - timedelta(days=31)
    _create_loan(client_id, LoanStatusEnum.APPROVED, approved_at=stale_approved_at)

    response = servicer.GetDashboardStats(
        dashboard_service_pb2.GetDashboardStatsRequest(), FakeContext()
    )
    assert response.approved_loans_count == 0
    assert response.expired_loans_count == 1

    with SessionLocal() as session:
        loan = session.query(Loan).filter(Loan.client_id == client_id).one()
        assert loan.status == LoanStatusEnum.EXPIRED
