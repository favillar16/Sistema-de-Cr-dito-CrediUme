"""La lista de cuotas de la Caja (2026-10-07).

Hasta esa fecha la única vista de las cuotas en la Caja era el texto de cada
opción del combo "Cuota a cobrar". Ahora hay una tabla con el cronograma
completo y su estado, sincronizada en los dos sentidos con el combo -- que
sigue siendo lo que decide qué se cobra. Estas pruebas recorren el widget real
(offscreen) porque lo que se mide es esa sincronización, no una función suelta.
"""

from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from cas_client import payoff, theme
from cas_client.session import Session
from cas_client.views.cash_view import _INST_COL_ESTADO, CashView, _estado_cuota

from tests.client.test_cobro_con_mora import _StubClient


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def caja(app):
    session = Session()
    session.access_token = "token"
    vista = CashView(
        client=_StubClient(),
        clients_client=_StubClient(),
        loans_client=_StubClient(),
        dashboard_client=_StubClient(),
        session=session,
    )
    vista.refresh = lambda: None
    vista._selected_loan = SimpleNamespace(id="loan-1", status="ACTIVE")
    return vista


_HOY = date.today()


def _cuota(numero, vence, pagada=False, mora="0.00", pendiente="100000.00"):
    return SimpleNamespace(
        installment_number=numero,
        due_date=vence.isoformat(),
        payment_amount="100000.00",
        amount_due="0.00" if pagada else pendiente,
        late_fee=mora,
        late_fee_days=0,
        is_paid=pagada,
    )


def _cronograma():
    return SimpleNamespace(
        installments=[
            _cuota(1, _HOY - timedelta(days=60), pagada=True),
            _cuota(2, _HOY - timedelta(days=30), mora="380.00"),
            _cuota(3, _HOY + timedelta(days=1)),
            _cuota(4, _HOY + timedelta(days=31)),
        ],
        remaining_balance="300000.00",
    )


def _estado(vista, fila) -> str:
    return vista._installment_table.item(fila, _INST_COL_ESTADO).text()


def test_la_tabla_muestra_todo_el_cronograma_con_su_estado(caja):
    caja._on_schedule_loaded(_cronograma())

    assert caja._installment_table.rowCount() == 4
    assert _estado(caja, 0) == "Pagada"
    assert _estado(caja, 1).startswith("Vencida hace 30 días")
    assert _estado(caja, 2) == "Próxima a vencer"
    assert _estado(caja, 3) == "Pendiente"
    # "A cobrar" es cuota + mora, el mismo total del campo de monto.
    assert caja._installment_table.item(1, 4).text() == "100.380 Gs"
    assert Decimal(caja._collection_amount.raw_value()) == Decimal("100380")


def test_una_cuota_pagada_no_se_puede_elegir(caja):
    caja._on_schedule_loaded(_cronograma())

    caja._installment_table.selectRow(0)

    assert caja._installment_combo.currentData()[0] == 2


def test_clic_en_una_cuota_la_elige_en_el_combo(caja):
    caja._on_schedule_loaded(_cronograma())

    caja._installment_table.selectRow(3)

    assert caja._installment_combo.currentData()[0] == 4


def test_elegir_en_el_combo_marca_la_fila(caja):
    caja._on_schedule_loaded(_cronograma())

    caja._installment_combo.setCurrentIndex(1)  # cuota 3

    filas = {i.row() for i in caja._installment_table.selectedIndexes()}
    assert filas == {2}


def test_el_pago_total_marca_todas_las_pendientes(caja):
    caja._on_schedule_loaded(_cronograma())

    caja._installment_combo.setCurrentIndex(caja._installment_combo.count() - 1)

    assert payoff.is_pay_in_full(caja._installment_combo.currentData())
    filas = {i.row() for i in caja._installment_table.selectedIndexes()}
    assert filas == {1, 2, 3}


def test_cambiar_de_cliente_vacia_la_tabla(caja):
    caja._on_schedule_loaded(_cronograma())

    caja._reset_collection_selection()

    assert caja._installment_table.rowCount() == 0


def test_un_pago_parcial_se_aclara_en_el_estado():
    cuota = _cuota(3, _HOY + timedelta(days=5), pendiente="40000.00")
    texto, color = _estado_cuota(cuota, _HOY, es_proxima=True)
    assert texto == "Próxima a vencer · pago parcial"
    assert color == theme.PRIMARY


def test_vencida_se_mide_por_fecha_aunque_no_haya_mora_todavia():
    """Durante los 5 días de gracia (BR-LOAN-017) la cuota ya está atrasada
    aunque todavía no devengue recargo."""
    cuota = _cuota(2, _HOY - timedelta(days=1))
    texto, color = _estado_cuota(cuota, _HOY, es_proxima=False)
    assert texto == "Vencida hace 1 día"
    assert color == theme.ERROR
