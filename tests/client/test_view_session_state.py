"""Reglas de invalidación del comprobante en las vistas de cobro.

Estos son los dos únicos tests del cliente que instancian widgets Qt de
verdad, y existen porque las dos reglas que cubren no viven en una función
pura: son estado de la vista entre un cobro y el papel que se imprime después.

    1. El comprobante pertenece a UN préstamo. Cambiar de préstamo en el combo
       tiene que apagarlo -- si no, el ticket sale con el N° de préstamo y el
       número de comprobante del préstamo nuevo y con el monto, las cuotas y
       la hora del cobro anterior.
    2. El comprobante pertenece a UNA sesión de operador. La vista se
       construye una vez y sobrevive al logout, así que un cambio de usuario
       tiene que borrarlo junto con los datos del cliente que quedaron en
       pantalla.

No usan pytest-qt (no es dependencia del proyecto): alcanza con una
QApplication offscreen y llamar a los handlers. Las llamadas gRPC se anulan
reemplazando _run_collection/_run_list, porque lo que se prueba es la regla de
invalidación, no la consulta.
"""

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from cas_client.session import Session  # noqa: E402
from cas_client.views.cash_view import CashView  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _loan(loan_id: str):
    return SimpleNamespace(
        id=loan_id,
        status="ACTIVE",
        term_months=12,
        remaining_balance="1000000.00",
        payment_status="AL_DIA",
        overdue_installments_count=0,
        overdue_amount="0.00",
    )


def _client_row():
    return SimpleNamespace(
        id="cli-1",
        first_name="Ana",
        last_name="Giménez",
        national_id="1234567",
        phone_number="0981000000",
    )


class _StubClient:
    """Cliente gRPC de mentira: cualquier método existe y no hace nada.

    Las vistas pasan el método como argumento a _run_collection (que acá está
    anulado), así que lo único que necesita es resolver el atributo.
    """

    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


@pytest.fixture
def view(app):
    session = Session()
    session.access_token = "token"
    vista = CashView(
        client=_StubClient(),
        clients_client=_StubClient(),
        loans_client=_StubClient(),
        session=session,
    )
    # El cobro, la carga del cronograma y el refresco de la caja salen por
    # acá; anularlos deja la vista sin red y sin hilos de trabajo.
    vista._run_collection = lambda *args, **kwargs: None
    vista.refresh = lambda: None
    return vista


def _cobrar(vista, loan):
    """Simula un cobro registrado sobre `loan`, como _on_collect -> RPC."""
    vista._selected_loan = loan
    vista._on_payment_recorded(
        SimpleNamespace(
            amount_paid="100000.00",
            covered_installments=[1],
            total_installments=12,
        )
    )


def _botones_habilitados(vista) -> bool:
    return all(boton.isEnabled() for boton in vista._receipt_buttons)


def test_el_comprobante_se_habilita_despues_de_cobrar(view):
    prestamo = _loan("loan-a")
    view._loan_combo.addItem("Préstamo A", prestamo)
    _cobrar(view, prestamo)

    assert view._comprobante_vigente()
    assert _botones_habilitados(view)


def test_cambiar_de_prestamo_apaga_el_comprobante_del_anterior(view):
    """El caso que motivó el pin: un cliente puede tener hasta 3 préstamos
    activos (BR-LOAN-001), y el ticket toma el N° de préstamo de la pantalla
    pero los montos del pago en memoria."""
    a, b = _loan("loan-a"), _loan("loan-b")
    view._loan_combo.addItem("Préstamo A", a)
    view._loan_combo.addItem("Préstamo B", b)
    view._loan_combo.setCurrentIndex(0)
    _cobrar(view, a)

    view._loan_combo.setCurrentIndex(1)

    assert view._selected_loan.id == "loan-b"
    assert not view._comprobante_vigente()
    assert not any(boton.isEnabled() for boton in view._receipt_buttons)


def test_volver_al_prestamo_cobrado_devuelve_el_comprobante(view):
    """No se descarta el pago, se apaga: el cajero suele mirar el otro
    préstamo del cliente antes de imprimir."""
    a, b = _loan("loan-a"), _loan("loan-b")
    view._loan_combo.addItem("Préstamo A", a)
    view._loan_combo.addItem("Préstamo B", b)
    view._loan_combo.setCurrentIndex(0)
    _cobrar(view, a)

    view._loan_combo.setCurrentIndex(1)
    view._loan_combo.setCurrentIndex(0)

    assert view._comprobante_vigente()
    assert _botones_habilitados(view)


def test_cambiar_de_cliente_apaga_el_comprobante(view):
    prestamo = _loan("loan-a")
    view._loan_combo.addItem("Préstamo A", prestamo)
    _cobrar(view, prestamo)

    view._reset_collection_selection()

    assert not view._comprobante_vigente()
    assert view._last_payment is None


def test_cambiar_de_operador_borra_el_cobro_y_los_datos_del_cliente(view):
    """Las PCs de ventanilla son compartidas: el cajero siguiente no puede
    encontrarse el comprobante del anterior habilitado ni su cliente en
    pantalla."""
    prestamo = _loan("loan-a")
    view._selected_client = _client_row()
    view._client_table.setRowCount(1)
    view._loan_combo.addItem("Préstamo A", prestamo)
    _cobrar(view, prestamo)
    assert _botones_habilitados(view)

    view.set_user("otro_cajero", "CASHIER")

    assert view._last_payment is None
    assert view._last_payment_loan_id is None
    assert view._selected_client is None
    assert view._selected_loan is None
    assert view._client_table.rowCount() == 0
    assert not view._client_table.isVisible()
    assert not any(boton.isEnabled() for boton in view._receipt_buttons)


def test_sin_cobro_los_papeles_no_se_emiten(view):
    """Guarda de fondo: los cuatro handlers salen temprano si el comprobante
    en memoria no es el del préstamo en pantalla."""
    a, b = _loan("loan-a"), _loan("loan-b")
    view._loan_combo.addItem("Préstamo A", a)
    view._loan_combo.addItem("Préstamo B", b)
    view._loan_combo.setCurrentIndex(0)
    _cobrar(view, a)
    view._loan_combo.setCurrentIndex(1)

    # Ninguno debe llegar a armar un documento ni abrir un diálogo.
    view._on_ticket_print()
    view._on_receipt_pdf()
    view._on_receipt_docx()
    view._on_receipt_print()
