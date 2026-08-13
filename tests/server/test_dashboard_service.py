from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import dashboard_service_pb2
import pytest

from cas_server.db.base import SessionLocal
from cas_server.db.models import Client, Loan, LoanStatusEnum
from cas_server.services.dashboard_service import DashboardServicer

from tests.server.helpers import FakeContext


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
):
    with SessionLocal() as session:
        loan = Loan(
            client_id=client_id,
            principal_amount=Decimal(principal),
            interest_rate=Decimal(rate),
            term_months=term,
            first_due_date=datetime.now(timezone.utc).date(),
            status=status,
            created_at=datetime.now(timezone.utc),
            approved_at=approved_at,
        )
        session.add(loan)
        session.commit()
        session.refresh(loan)
        return loan.id


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
