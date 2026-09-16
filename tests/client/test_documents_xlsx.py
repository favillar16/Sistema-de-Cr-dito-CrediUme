"""Pure-function coverage for documents_xlsx.py -- the "cartera por vencer"
Excel export (GetUpcomingDueReport). No Qt/gRPC/DB: fakes the response with
SimpleNamespace (same convention as test_view_session_state.py's _loan()/
_client_row()) and reads the workbook back with openpyxl."""

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from cas_client import documents_xlsx


def _timestamp(when: datetime):
    return SimpleNamespace(ToDatetime=lambda: when)


def _row(**kwargs):
    defaults = dict(
        client_id="c1",
        client_name="Ana Giménez",
        phone_number="0981000000",
        loan_id="11111111-2222-3333-4444-555555555555",
        installment_number=2,
        due_date="2026-09-20",
        due_amount="150000.00",
        installments_paid_count=1,
        term_months=12,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _response(rows=(), days_ahead=7):
    return SimpleNamespace(
        generated_at=_timestamp(datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)),
        days_ahead=days_ahead,
        rows=list(rows),
    )


def _hoja(response):
    return documents_xlsx.reporte_cartera_por_vencer_workbook(response).active


def test_workbook_has_one_row_per_installment():
    hoja = _hoja(_response(rows=[_row(), _row(client_name="Beto López")]))

    nombres = [hoja.cell(row=fila, column=1).value for fila in (5, 6)]
    assert nombres == ["Ana Giménez", "Beto López"]


def test_amount_and_date_are_real_typed_cells_not_display_strings():
    """El punto de exportar a Excel en vez de PDF es que las columnas queden
    ordenables/sumables -- si el monto o la fecha llegaran como texto ya
    formateado (como formatting.gs()/formatting.fecha()), Excel no podría
    sumar ni ordenar la columna sin que el usuario la reformatee primero."""
    hoja = _hoja(_response(rows=[_row(due_amount="150000.00", due_date="2026-09-20")]))

    assert hoja.cell(row=5, column=5).value == date(2026, 9, 20)
    assert hoja.cell(row=5, column=6).value == Decimal("150000.00")


def test_loan_id_is_shortened_like_the_rest_of_the_app():
    """Mismo criterio que el combo de préstamos de cash_view.py
    (`loan.id[:8]`): el UUID completo no aporta nada legible en la planilla."""
    hoja = _hoja(_response(rows=[_row(loan_id="11111111-2222-3333-4444-555555555555")]))

    assert hoja.cell(row=5, column=3).value == "11111111"


def test_empty_report_says_so_instead_of_an_empty_sheet():
    hoja = _hoja(_response(rows=[]))

    assert "por vencer" in hoja.cell(row=5, column=1).value.lower()


def test_title_states_the_window_used():
    hoja = _hoja(_response(rows=[], days_ahead=7))

    assert "7 días" in hoja["A1"].value


def test_generated_at_is_shown_in_local_time_not_utc():
    """Mismo criterio que el resto de la app (formatting.a_hora_local): la
    marca de tiempo del reporte no debe imprimirse en UTC crudo."""
    hoja = _hoja(_response(rows=[]))

    assert "2026" in hoja["A2"].value
    assert "Generado" in hoja["A2"].value
