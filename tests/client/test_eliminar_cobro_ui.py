"""BR-LOAN-019 en las dos pantallas que muestran el historial de cobros.

Igual que test_cobro_con_mora.py: la regla tiene dos puertas (Caja y el
detalle del préstamo) y la falla barata es cablear una y olvidar la otra. Se
recorre el camino real del widget: cargar el historial, elegir la fila,
apretar "Eliminar cobro" y ver qué se le manda al servidor.
"""

from types import SimpleNamespace

import loan_service_pb2
import pytest
from PySide6.QtWidgets import QApplication

from cas_client import payment_deletion
from cas_client.session import Session
from cas_client.views.cash_view import CashView
from cas_client.views.loans_view import LoansView


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _StubClient:
    def __getattr__(self, name):
        def _llamada(*args, **kwargs):
            return None

        _llamada.__name__ = name
        return _llamada


def _historial():
    return loan_service_pb2.ListLoanPaymentsResponse(
        payments=[
            loan_service_pb2.LoanPaymentEntry(
                id="pago-nuevo",
                amount="100.00",
                total_charged="100.00",
                payment_method="EFECTIVO",
                covered_installments=[2],
            ),
            loan_service_pb2.LoanPaymentEntry(
                id="pago-viejo",
                amount="100.00",
                total_charged="100.00",
                payment_method="TRANSFERENCIA",
                covered_installments=[1],
            ),
        ],
        total_installments=12,
    )


def _session(role):
    session = Session()
    session.access_token = "token"
    session.role = role
    return session


@pytest.fixture
def caja(app):
    vista = CashView(
        client=_StubClient(),
        clients_client=_StubClient(),
        loans_client=_StubClient(),
        dashboard_client=_StubClient(),
        session=_session("CASHIER"),
    )
    vista.refresh = lambda: None
    vista._selected_loan = SimpleNamespace(id="loan-1", status="ACTIVE")
    return vista


@pytest.fixture
def prestamos(app):
    vista = LoansView(
        client=_StubClient(), client_service=_StubClient(), session=_session("CASHIER")
    )
    vista._selected_loan_id = "loan-1"
    return vista


@pytest.fixture
def motivo(monkeypatch):
    preguntas = []

    def _preguntar(parent, entry, total, *, is_latest):
        preguntas.append((entry.id, total, is_latest))
        return "Cobro duplicado"

    monkeypatch.setattr(payment_deletion, "ask_delete_reason", _preguntar)
    return preguntas


def test_caja_el_boton_espera_a_que_se_elija_un_cobro(caja):
    caja._on_payments_loaded(_historial())
    assert not caja._delete_payment_button.isEnabled()

    caja._payments_table.selectRow(0)

    # El cajero también puede: decisión de la entidad (BR-LOAN-019).
    assert caja._delete_payment_button.isEnabled()


def test_caja_manda_el_id_del_cobro_elegido_y_el_motivo(caja, motivo):
    llamadas = []
    caja._run_collection = lambda fn, *args, **kwargs: llamadas.append((fn, args))
    caja._on_payments_loaded(_historial())
    caja._payments_table.selectRow(1)

    caja._on_delete_payment()

    assert motivo == [("pago-viejo", 12, False)]
    fn, args = llamadas[0]
    assert fn.__name__ == "delete_loan_payment"
    assert args == ("token", "pago-viejo", "Cobro duplicado")


def test_caja_borrar_el_cobro_recien_hecho_invalida_su_ticket(caja):
    caja._last_payment = SimpleNamespace(payment_id="pago-nuevo")
    caja._last_payment_loan_id = "loan-1"
    caja._reload_selected_loan = lambda: None

    caja._on_payment_deleted(
        "pago-nuevo",
        loan_service_pb2.DeleteLoanPaymentResponse(cash_amount_removed="100.00"),
    )

    assert caja._last_payment is None
    assert not any(boton.isEnabled() for boton in caja._receipt_buttons)


def test_prestamos_manda_el_id_del_cobro_elegido(prestamos, motivo):
    llamadas = []
    prestamos._run_loan_action = lambda fn, *args, **kwargs: llamadas.append((fn, args))
    prestamos._on_payments_loaded(_historial())
    prestamos._payments_table.selectRow(0)
    assert prestamos._delete_payment_button.isEnabled()

    prestamos._on_delete_payment()

    assert motivo == [("pago-nuevo", 12, True)]
    fn, args = llamadas[0]
    assert fn.__name__ == "delete_loan_payment"
    assert args == ("pago-nuevo", "Cobro duplicado")


def test_cancelar_no_llama_al_servidor(prestamos, monkeypatch):
    monkeypatch.setattr(payment_deletion, "ask_delete_reason", lambda *a, **k: None)
    llamadas = []
    prestamos._run_loan_action = lambda *a, **k: llamadas.append(a)
    prestamos._on_payments_loaded(_historial())
    prestamos._payments_table.selectRow(0)

    prestamos._on_delete_payment()

    assert llamadas == []
