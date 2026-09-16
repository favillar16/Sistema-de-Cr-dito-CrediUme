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
    disbursed_at=None,
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
            disbursed_at=disbursed_at,
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


def _primeras_cuotas(cantidad, principal="1000.00", rate="0.12", term=6):
    """Suma de las primeras `cantidad` cuotas del cronograma, para no
    hardcodear en los asserts el resultado de la misma matemática que el
    servidor usa.

    Bajo el sistema alemán (BR-LOAN-013) las cuotas **no** son iguales entre
    sí, así que dos cuotas no son "la primera por 2": hay que sumarlas.
    """
    cronograma = calcular_cronograma(Decimal(principal), Decimal(rate), term)
    return sum(fila.monto_cuota for fila in cronograma[:cantidad])


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
    assert Decimal(response.total_overdue_amount) == _primeras_cuotas(2)
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
    _record_payment(loan_id, str(_primeras_cuotas(2)))

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


def test_period_report_totals_what_was_disbursed_in_the_range(servicer):
    """BR-DASH-002: el desembolso se cuenta por Loan.disbursed_at y no por el
    estado, porque un ACTIVE de hoy puede haberse desembolsado en cualquier
    período anterior.

    Es lo único del bloque que mide plata que salió: `principal_approved` es
    una decisión, no una salida de caja.
    """
    client_id = _create_client("8000031", "dash_disb@example.com")
    hoy = datetime.now(timezone.utc)
    dentro = hoy - timedelta(days=2)
    fuera = hoy - timedelta(days=90)

    _create_loan(
        client_id,
        LoanStatusEnum.ACTIVE,
        created_at=fuera,
        approved_at=fuera,
        disbursed_at=dentro,
        principal="3000.00",
    )
    # Desembolsado hace meses: sigue ACTIVE, pero no es de este período.
    _create_loan(
        client_id,
        LoanStatusEnum.ACTIVE,
        created_at=fuera,
        approved_at=fuera,
        disbursed_at=fuera,
        principal="5000.00",
    )
    # Aprobado dentro del rango pero todavía sin desembolsar: cuenta como
    # aprobación, no como desembolso. Esa es exactamente la distinción que el
    # reporte no podía hacer antes de que existiera disbursed_at.
    _create_loan(
        client_id,
        LoanStatusEnum.APPROVED,
        created_at=dentro,
        approved_at=dentro,
        principal="9000.00",
    )

    response = servicer.GetPeriodReport(
        dashboard_service_pb2.GetPeriodReportRequest(
            start_date=(hoy - timedelta(days=7)).date().isoformat(),
            end_date=hoy.date().isoformat(),
        ),
        FakeContext(),
    )

    assert response.loans_disbursed == 1
    assert Decimal(response.principal_disbursed) == Decimal("3000.00")
    # Aprobado en el rango hay UNO solo: el de 9.000, que todavía no se
    # desembolsó. El de 3.000 se aprobó hace 90 días y se desembolsó recién
    # ahora -- contarlo como aprobación de este período borraría justamente la
    # distinción que esta regla existe para hacer.
    assert response.loans_approved == 1
    assert Decimal(response.principal_approved) == Decimal("9000.00")


def test_period_report_ignores_loans_disbursed_before_the_column_existed(servicer):
    """La migración no hace backfill: un préstamo desembolsado antes de que
    existiera disbursed_at queda en NULL. Se lo deja fuera de todo período en
    vez de atribuirlo a uno inventado -- el total histórico sigue estando en
    GetDashboardStats.total_disbursed, que no depende de esta columna."""
    client_id = _create_client("8000032", "dash_disb_null@example.com")
    hoy = datetime.now(timezone.utc)
    _create_loan(
        client_id,
        LoanStatusEnum.ACTIVE,
        created_at=hoy - timedelta(days=1),
        approved_at=hoy - timedelta(days=1),
        disbursed_at=None,
        principal="4000.00",
    )

    response = servicer.GetPeriodReport(
        dashboard_service_pb2.GetPeriodReportRequest(
            start_date=(hoy - timedelta(days=7)).date().isoformat(),
            end_date=hoy.date().isoformat(),
        ),
        FakeContext(),
    )
    assert response.loans_disbursed == 0
    assert Decimal(response.principal_disbursed) == Decimal("0.00")
    # Pero sigue contando en la cartera desembolsada histórica del panel.
    stats = servicer.GetDashboardStats(
        dashboard_service_pb2.GetDashboardStatsRequest(), FakeContext()
    )
    assert Decimal(stats.total_disbursed) == Decimal("4000.00")


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


# ---- BR-DASH-003: estado de pago de los clientes -------------------------


def _estado_pago(servicer, only_overdue=False):
    return servicer.GetClientPaymentStatusReport(
        dashboard_service_pb2.GetClientPaymentStatusReportRequest(
            only_overdue=only_overdue
        ),
        FakeContext(),
    )


def test_payment_status_lists_only_clients_with_a_live_portfolio(servicer):
    """Un cliente sin préstamos, o con todos pagados, no tiene estado de pago
    que informar: aparecería como una fila en cero que solo alarga la lista de
    cobranza."""
    con_activo = _create_client("8100001", "ps_active@example.com")
    _create_loan(con_activo, LoanStatusEnum.ACTIVE)
    con_pagado = _create_client("8100002", "ps_paid@example.com")
    _create_loan(con_pagado, LoanStatusEnum.PAID)
    _create_client("8100003", "ps_none@example.com")  # sin préstamos

    response = _estado_pago(servicer)

    assert [fila.client_id for fila in response.rows] == [str(con_activo)]
    assert response.clients_count == 1


def test_payment_status_marks_a_client_up_to_date_as_al_dia(servicer):
    client_id = _create_client("8100004", "ps_aldia@example.com")
    # Primer vencimiento en el futuro: todavía no venció ninguna cuota.
    futuro = datetime.now(timezone.utc).date() + timedelta(days=10)
    _create_loan(client_id, LoanStatusEnum.ACTIVE, first_due_date=futuro)

    (fila,) = _estado_pago(servicer).rows

    assert fila.payment_status == "AL_DIA"
    assert Decimal(fila.overdue_amount) == Decimal("0.00")
    assert fila.overdue_installments_count == 0
    # Sigue debiendo el préstamo entero: "al día" no es "sin saldo".
    assert Decimal(fila.outstanding_balance) > Decimal("0.00")


def test_payment_status_reports_overdue_amount_and_installment_count(servicer):
    client_id = _create_client("8100005", "ps_mora@example.com")
    primer_vencimiento = datetime.now(timezone.utc).date() - timedelta(days=40)
    _create_loan(client_id, LoanStatusEnum.ACTIVE, first_due_date=primer_vencimiento)

    (fila,) = _estado_pago(servicer).rows

    assert fila.payment_status == "CUOTA_VENCIDA"
    assert Decimal(fila.overdue_amount) == _primeras_cuotas(2)
    assert fila.overdue_installments_count == 2
    # La mora es un subconjunto del saldo, nunca al revés (BR-DASH-001).
    assert Decimal(fila.overdue_amount) < Decimal(fila.outstanding_balance)


def test_payment_status_next_due_is_the_earliest_unpaid_installment(servicer):
    """Lo que necesita quien llama al cliente: cuál es la próxima cuota que
    tiene que cobrar, no la primera del cronograma."""
    client_id = _create_client("8100006", "ps_proxima@example.com")
    primer_vencimiento = datetime.now(timezone.utc).date() - timedelta(days=40)
    loan_id = _create_loan(
        client_id, LoanStatusEnum.ACTIVE, first_due_date=primer_vencimiento
    )
    # Paga exactamente la cuota 1: la próxima impaga pasa a ser la 2.
    _record_payment(loan_id, str(_primeras_cuotas(1)))

    (fila,) = _estado_pago(servicer).rows

    cronograma = calcular_cronograma(
        Decimal("1000.00"),
        Decimal("0.12"),
        6,
        fecha_primer_vencimiento=primer_vencimiento,
    )
    assert fila.next_due_date == cronograma[1].fecha_vencimiento.isoformat()
    assert Decimal(fila.next_due_amount) == cronograma[1].monto_cuota
    assert fila.overdue_installments_count == 1


def test_payment_status_defaulted_takes_precedence_over_the_schedule(servicer):
    """INCUMPLIDO lo declaró un operador (MarkDefaulted); "al día" es una
    deducción del cronograma. Informar "al día" a alguien con un préstamo
    incumplido sería el peor de los dos errores posibles."""
    client_id = _create_client("8100007", "ps_default@example.com")
    futuro = datetime.now(timezone.utc).date() + timedelta(days=10)
    _create_loan(client_id, LoanStatusEnum.ACTIVE, first_due_date=futuro)
    _create_loan(client_id, LoanStatusEnum.DEFAULTED)

    (fila,) = _estado_pago(servicer).rows

    assert fila.payment_status == "INCUMPLIDO"
    assert fila.active_loans_count == 1
    assert fila.defaulted_loans_count == 1


def test_payment_status_only_overdue_filter_drops_the_clients_up_to_date(servicer):
    al_dia = _create_client("8100008", "ps_filtro_aldia@example.com")
    futuro = datetime.now(timezone.utc).date() + timedelta(days=10)
    _create_loan(al_dia, LoanStatusEnum.ACTIVE, first_due_date=futuro)
    en_mora = _create_client("8100009", "ps_filtro_mora@example.com")
    _create_loan(
        en_mora,
        LoanStatusEnum.ACTIVE,
        first_due_date=datetime.now(timezone.utc).date() - timedelta(days=40),
    )

    completo = _estado_pago(servicer, only_overdue=False)
    filtrado = _estado_pago(servicer, only_overdue=True)

    assert {fila.client_id for fila in completo.rows} == {str(al_dia), str(en_mora)}
    assert [fila.client_id for fila in filtrado.rows] == [str(en_mora)]
    assert filtrado.only_overdue is True


def test_payment_status_totals_describe_the_rows_returned_not_the_portfolio(servicer):
    """Con el filtro puesto, los totales son los del subconjunto -- por eso no
    tienen por qué coincidir con los del panel, y el documento lo aclara."""
    al_dia = _create_client("8100010", "ps_tot_aldia@example.com")
    futuro = datetime.now(timezone.utc).date() + timedelta(days=10)
    _create_loan(al_dia, LoanStatusEnum.ACTIVE, first_due_date=futuro)
    en_mora = _create_client("8100011", "ps_tot_mora@example.com")
    _create_loan(
        en_mora,
        LoanStatusEnum.ACTIVE,
        first_due_date=datetime.now(timezone.utc).date() - timedelta(days=40),
    )

    completo = _estado_pago(servicer, only_overdue=False)
    filtrado = _estado_pago(servicer, only_overdue=True)

    assert completo.clients_count == 2
    assert filtrado.clients_count == 1
    assert Decimal(filtrado.total_outstanding) < Decimal(completo.total_outstanding)
    # La mora, en cambio, es la misma: el cliente al día no aportaba nada.
    assert Decimal(filtrado.total_overdue) == Decimal(completo.total_overdue)


def test_payment_status_overdue_total_matches_the_dashboard(servicer):
    """La misma cifra abierta por cliente y sumada tiene que dar el
    total_overdue_amount de BR-DASH-001 -- si divergieran, una de las dos
    pantallas estaría mintiendo."""
    primero = _create_client("8100012", "ps_match_a@example.com")
    segundo = _create_client("8100013", "ps_match_b@example.com")
    atrasado = datetime.now(timezone.utc).date() - timedelta(days=40)
    _create_loan(primero, LoanStatusEnum.ACTIVE, first_due_date=atrasado)
    _create_loan(segundo, LoanStatusEnum.ACTIVE, first_due_date=atrasado)

    reporte = _estado_pago(servicer)
    panel = servicer.GetDashboardStats(
        dashboard_service_pb2.GetDashboardStatsRequest(), FakeContext()
    )

    assert Decimal(reporte.total_overdue) == Decimal(panel.total_overdue_amount)
    assert Decimal(reporte.total_outstanding) == Decimal(
        panel.total_outstanding_balance
    )


def test_payment_status_orders_the_worst_debtors_first(servicer):
    poco = _create_client("8100014", "ps_orden_poco@example.com")
    mucho = _create_client("8100015", "ps_orden_mucho@example.com")
    _create_loan(
        poco,
        LoanStatusEnum.ACTIVE,
        first_due_date=datetime.now(timezone.utc).date() - timedelta(days=5),
    )
    _create_loan(
        mucho,
        LoanStatusEnum.ACTIVE,
        first_due_date=datetime.now(timezone.utc).date() - timedelta(days=100),
    )

    filas = _estado_pago(servicer).rows

    montos = [Decimal(fila.overdue_amount) for fila in filas]
    assert montos == sorted(montos, reverse=True)
    assert filas[0].client_id == str(mucho)


def test_payment_status_expires_stale_approved_loans_lazily(servicer):
    """Igual que el resto de las lecturas de préstamos (BR-LOAN-003)."""
    client_id = _create_client("8100016", "ps_expira@example.com")
    loan_id = _create_loan(
        client_id,
        LoanStatusEnum.APPROVED,
        approved_at=datetime.now(timezone.utc) - timedelta(days=31),
    )

    _estado_pago(servicer)

    with SessionLocal() as session:
        assert session.get(Loan, loan_id).status == LoanStatusEnum.EXPIRED


# ---- Cartera por vencer (pantalla de Caja) --------------------------------


def _cartera_por_vencer(servicer, days_ahead=7):
    return servicer.GetUpcomingDueReport(
        dashboard_service_pb2.GetUpcomingDueReportRequest(days_ahead=days_ahead),
        FakeContext(),
    )


def test_upcoming_due_lists_an_installment_due_within_the_window(servicer):
    client_id = _create_client("8200001", "up_dentro@example.com")
    vencimiento = datetime.now(timezone.utc).date() + timedelta(days=3)
    loan_id = _create_loan(client_id, LoanStatusEnum.ACTIVE, first_due_date=vencimiento)

    (fila,) = _cartera_por_vencer(servicer).rows

    assert fila.client_id == str(client_id)
    assert fila.loan_id == str(loan_id)
    assert fila.installment_number == 1
    assert fila.due_date == vencimiento.isoformat()
    assert Decimal(fila.due_amount) == _primeras_cuotas(1)
    assert fila.installments_paid_count == 0
    assert fila.term_months == 6


def test_upcoming_due_excludes_installments_outside_the_window(servicer):
    client_id = _create_client("8200002", "up_afuera@example.com")
    # Vence en 20 días: fuera de la ventana de 7.
    lejos = datetime.now(timezone.utc).date() + timedelta(days=20)
    _create_loan(client_id, LoanStatusEnum.ACTIVE, first_due_date=lejos)

    assert list(_cartera_por_vencer(servicer).rows) == []


def test_upcoming_due_excludes_installments_already_overdue(servicer):
    """ "Por vencer" no es "vencida" -- eso ya lo cubre BR-DASH-003
    (GetClientPaymentStatusReport). Una cuota que ya pasó su fecha no
    pertenece a este reporte, aunque siga impaga."""
    client_id = _create_client("8200003", "up_vencida@example.com")
    atrasado = datetime.now(timezone.utc).date() - timedelta(days=5)
    _create_loan(client_id, LoanStatusEnum.ACTIVE, first_due_date=atrasado)

    assert list(_cartera_por_vencer(servicer).rows) == []


def test_upcoming_due_includes_the_boundary_day(servicer):
    """Ventana inclusiva en el extremo: una cuota que vence justo al día
    número `days_ahead` cuenta como "por vencer"."""
    client_id = _create_client("8200004", "up_limite@example.com")
    limite = datetime.now(timezone.utc).date() + timedelta(days=7)
    _create_loan(client_id, LoanStatusEnum.ACTIVE, first_due_date=limite)

    (fila,) = _cartera_por_vencer(servicer, days_ahead=7).rows
    assert fila.due_date == limite.isoformat()


def test_upcoming_due_only_considers_active_loans(servicer):
    """PENDING/APPROVED todavía no tienen cuotas exigibles y DEFAULTED ya no
    las tiene -- mismo criterio que _COBRABLE del lado del cliente."""
    client_id = _create_client("8200005", "up_estado@example.com")
    pronto = datetime.now(timezone.utc).date() + timedelta(days=2)
    _create_loan(client_id, LoanStatusEnum.PENDING, first_due_date=pronto)
    _create_loan(client_id, LoanStatusEnum.APPROVED, first_due_date=pronto)
    _create_loan(client_id, LoanStatusEnum.DEFAULTED, first_due_date=pronto)

    assert list(_cartera_por_vencer(servicer).rows) == []


def test_upcoming_due_excludes_an_installment_already_paid(servicer):
    client_id = _create_client("8200006", "up_pagada@example.com")
    vencimiento = datetime.now(timezone.utc).date() + timedelta(days=2)
    loan_id = _create_loan(client_id, LoanStatusEnum.ACTIVE, first_due_date=vencimiento)
    _record_payment(loan_id, str(_primeras_cuotas(1)))

    assert list(_cartera_por_vencer(servicer).rows) == []


def test_upcoming_due_reports_how_many_installments_are_already_paid(servicer):
    """El conteo tiene que reflejar lo YA cubierto del préstamo, no solo de
    la cuota que está por vencer -- por eso se ancla en el cronograma real en
    vez de asumir una fecha de vencimiento de memoria."""
    client_id = _create_client("8200007", "up_conteo@example.com")
    # 25 días atrás: sea cual sea el mes, la cuota 2 (un mes después) cae
    # entre 3 y 6 días en el futuro (28 a 31 días de mes, menos 25).
    primer_vencimiento = datetime.now(timezone.utc).date() - timedelta(days=25)
    loan_id = _create_loan(
        client_id, LoanStatusEnum.ACTIVE, first_due_date=primer_vencimiento
    )
    _record_payment(loan_id, str(_primeras_cuotas(1)))

    cronograma = calcular_cronograma(
        Decimal("1000.00"),
        Decimal("0.12"),
        6,
        fecha_primer_vencimiento=primer_vencimiento,
    )
    segundo_vencimiento = cronograma[1].fecha_vencimiento
    dias = (segundo_vencimiento - datetime.now(timezone.utc).date()).days

    (fila,) = _cartera_por_vencer(servicer, days_ahead=dias).rows

    assert fila.installment_number == 2
    assert fila.installments_paid_count == 1
    assert fila.due_date == segundo_vencimiento.isoformat()


def test_upcoming_due_defaults_to_seven_days_when_unspecified(servicer):
    client_id = _create_client("8200008", "up_default@example.com")
    vencimiento = datetime.now(timezone.utc).date() + timedelta(days=6)
    _create_loan(client_id, LoanStatusEnum.ACTIVE, first_due_date=vencimiento)

    response = servicer.GetUpcomingDueReport(
        dashboard_service_pb2.GetUpcomingDueReportRequest(), FakeContext()
    )
    assert response.days_ahead == 7
    assert len(response.rows) == 1


def test_upcoming_due_orders_rows_by_due_date(servicer):
    lejos = _create_client("8200009", "up_orden_lejos@example.com")
    _create_loan(
        lejos,
        LoanStatusEnum.ACTIVE,
        first_due_date=datetime.now(timezone.utc).date() + timedelta(days=6),
    )
    cerca = _create_client("8200010", "up_orden_cerca@example.com")
    _create_loan(
        cerca,
        LoanStatusEnum.ACTIVE,
        first_due_date=datetime.now(timezone.utc).date() + timedelta(days=1),
    )

    filas = _cartera_por_vencer(servicer).rows

    assert [fila.client_id for fila in filas] == [str(cerca), str(lejos)]
