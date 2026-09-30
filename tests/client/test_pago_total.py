"""BR-LOAN-018: pago total del préstamo con descuento, del lado del cliente.

Mismo criterio que test_cobro_con_mora.py: las **dos** entradas de cobro
(CashView y LoansView) se prueban juntas y por el camino real del widget --
poblar el combo, elegir "Pago total", escribir el descuento y apretar el
botón --, porque la forma barata de romper la regla es arreglar una pantalla y
olvidar la otra. Y los papeles (comprobante, ticket ESC/POS, reimpresión)
tienen que decir lo que el cliente entregó, no el saldo entero imputado.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from cas_client import documents, escpos, payoff
from cas_client.session import Session
from cas_client.views.cash_view import CashView
from cas_client.views.loans_view import LoansView

from tests.client.test_cobro_con_mora import _CRONOGRAMA, _StubClient, _monto


# Mismos fixtures que test_cobro_con_mora.py, repetidos y no importados: los
# tests del cliente corren con --noconftest, y un fixture importado de otro
# módulo es un F811 para flake8.
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


@pytest.fixture
def prestamos(app):
    session = Session()
    session.access_token = "token"
    vista = LoansView(
        client=_StubClient(),
        client_service=_StubClient(),
        session=session,
    )
    vista._selected_loan_id = "loan-1"
    return vista


# _CRONOGRAMA: saldo 480.000, mora 1.064 (sólo en la cuota 1).
_SALDO = Decimal("480000")
_MORA = Decimal("1064")


def _elegir_pago_total(combo) -> None:
    combo.setCurrentIndex(combo.count() - 1)
    assert payoff.is_pay_in_full(combo.currentData())


# ---- Funciones puras -------------------------------------------------------


def test_la_opcion_de_pago_total_suma_saldo_y_mora_del_cronograma():
    etiqueta, datos = payoff.payoff_item(_CRONOGRAMA)

    assert datos == (payoff.PAY_IN_FULL, "480000.00", "1064.00")
    assert "Pago total" in etiqueta and "mora" in etiqueta


def test_sin_saldo_no_hay_opcion_de_pago_total():
    cronograma = SimpleNamespace(installments=[], remaining_balance="0.00")
    assert payoff.payoff_item(cronograma) is None


def test_el_total_resta_el_descuento_y_suma_la_mora():
    assert payoff.amount_to_collect("480000", "1064", "30000") == Decimal("451064")


def test_un_descuento_fuera_de_rango_no_se_resta_del_total_mostrado():
    """El error se informa aparte; el total nunca muestra un número que el
    servidor va a rechazar."""
    assert payoff.amount_to_collect("480000", "0", "480000") == _SALDO


def test_el_descuento_tiene_que_dejar_algo_del_saldo():
    assert payoff.discount_error("", "480000.00") is None
    assert payoff.discount_error("479999", "480000.00") is None
    assert payoff.discount_error("480000", "480000.00") is not None
    assert payoff.discount_error("500000", "480000.00") is not None


def test_el_desglose_dice_que_la_mora_no_se_descuenta():
    texto = payoff.breakdown_text("480000", "1064", "30000", True)
    assert "descuento" in texto and "mora" in texto
    assert "451.064" in texto


# ---- Caja ------------------------------------------------------------------


def test_la_caja_ofrece_el_pago_total_al_final(caja):
    caja._on_schedule_loaded(_CRONOGRAMA)

    # Lo normal sigue siendo cobrar la cuota más vieja.
    assert caja._installment_combo.currentData()[0] == 1
    assert not caja._collection_discount.isEnabled()
    _elegir_pago_total(caja._installment_combo)
    assert caja._collection_discount.isEnabled()
    assert _monto(caja._collection_amount) == _SALDO + _MORA


def test_la_caja_descuenta_al_escribir(caja):
    caja._on_schedule_loaded(_CRONOGRAMA)
    _elegir_pago_total(caja._installment_combo)

    caja._collection_discount.setText("30.000")

    assert _monto(caja._collection_amount) == _SALDO - Decimal("30000") + _MORA
    assert "descuento" in caja._collection_breakdown.text()


def test_volver_a_una_cuota_borra_el_descuento(caja):
    caja._on_schedule_loaded(_CRONOGRAMA)
    _elegir_pago_total(caja._installment_combo)
    caja._collection_discount.setText("30.000")

    caja._installment_combo.setCurrentIndex(0)

    assert caja._collection_discount.raw_value() == ""
    assert not caja._collection_discount.isEnabled()
    assert _monto(caja._collection_amount) == Decimal("241064")


def test_la_caja_manda_el_pago_total_con_su_descuento(caja):
    caja._on_schedule_loaded(_CRONOGRAMA)
    _elegir_pago_total(caja._installment_combo)
    caja._collection_discount.setText("30.000")
    enviado = {}
    caja._run_collection = lambda fn, *a, on_success, **k: enviado.update(k)

    caja._on_collect()

    assert enviado["pay_in_full"] is True
    assert enviado["discount_amount"] == "30000"
    # Nunca el número ficticio de la opción: el servidor recibe el modo.
    assert enviado["installment_number"] == 0


def test_la_caja_no_manda_un_descuento_que_se_come_el_saldo(caja):
    caja._on_schedule_loaded(_CRONOGRAMA)
    _elegir_pago_total(caja._installment_combo)
    caja._collection_discount.setText("480.000")
    enviado = {}
    caja._run_collection = lambda fn, *a, on_success, **k: enviado.update(k)

    caja._on_collect()

    assert enviado == {}
    assert caja._collect_button.isEnabled()


def test_un_cobro_por_cuota_no_manda_descuento(caja):
    caja._on_schedule_loaded(_CRONOGRAMA)
    enviado = {}
    caja._run_collection = lambda fn, *a, on_success, **k: enviado.update(k)

    caja._on_collect()

    assert enviado["pay_in_full"] is False
    assert enviado["discount_amount"] == ""
    assert enviado["installment_number"] == 1


# ---- Detalle del préstamo --------------------------------------------------


def test_el_detalle_manda_el_pago_total_con_su_descuento(prestamos):
    prestamos._on_pending_installments_loaded(_CRONOGRAMA)
    prestamos._record_payment_button.setEnabled(True)
    _elegir_pago_total(prestamos._payment_installment_combo)
    prestamos._payment_discount_input.setText("30.000")
    prestamos._payment_reference_input.setText("TRF-1")
    enviado = {}
    prestamos._run_loan_action = lambda fn, *a, on_success, **k: enviado.update(k)

    prestamos._on_record_payment()

    assert enviado["pay_in_full"] is True
    assert enviado["discount_amount"] == "30000"
    assert enviado["installment_number"] == 0


def test_las_dos_pantallas_muestran_el_mismo_total_con_descuento(caja, prestamos):
    caja._on_schedule_loaded(_CRONOGRAMA)
    prestamos._on_pending_installments_loaded(_CRONOGRAMA)
    for combo, campo in (
        (caja._installment_combo, caja._collection_discount),
        (prestamos._payment_installment_combo, prestamos._payment_discount_input),
    ):
        _elegir_pago_total(combo)
        campo.setText("30.000")

    assert (
        _monto(caja._collection_amount)
        == _monto(prestamos._payment_amount_display)
        == _SALDO - Decimal("30000") + _MORA
    )


# ---- Papeles ---------------------------------------------------------------


def _cobro_total(**cambios):
    datos = dict(
        amount_paid="480000.00",
        discount_amount="30000.00",
        late_fee_amount="1064.00",
        total_charged="451064.00",
        paid_in_full=True,
    )
    datos.update(cambios)
    return SimpleNamespace(**datos)


def test_el_comprobante_desglosa_saldo_descuento_mora_y_total():
    filas = dict(documents.filas_cobro(_cobro_total()))

    assert filas["Saldo cancelado"] == documents.gs("480000.00")
    assert "30.000" in filas["Descuento por pago total"]
    assert filas["Mora por atraso"] == documents.gs("1064.00")
    assert filas["Total abonado"] == documents.gs("451064.00")
    assert documents.filas_cobro(_cobro_total())[-1][0] == "Total abonado"


def test_sin_descuento_ni_mora_el_comprobante_no_cambia():
    cobro = _cobro_total(discount_amount="0.00", late_fee_amount="0.00")
    assert documents.filas_cobro(cobro) == [
        ("Importe abonado", documents.gs("480000.00"))
    ]


def test_con_descuento_el_acumulado_no_dice_pagado():
    assert "cancelado" in documents.etiqueta_total_pagado(_cobro_total())
    cobro_normal = _cobro_total(discount_amount="")
    assert "pagado" in documents.etiqueta_total_pagado(cobro_normal)


def test_la_reimpresion_conserva_el_descuento():
    entry = SimpleNamespace(
        amount="480000.00",
        late_fee_amount="",
        discount_amount="30000.00",
        total_charged="450000.00",
        covered_installments=[1, 2],
        paid_at=None,
        payment_method="EFECTIVO",
        transfer_reference="",
        total_paid_after="480000.00",
        remaining_balance_after="0.00",
        recorded_by_name="",
        recorded_by_national_id="",
    )

    pago = documents.CobroHistorico(entry, 2)

    assert pago.paid_in_full is True
    assert ("Total abonado", documents.gs("450000.00")) in documents.filas_cobro(pago)


def test_la_ficha_del_prestamo_no_dice_pagado_cuando_hubo_descuento():
    con_descuento = SimpleNamespace(total_paid="480000.00", total_discount="30000.00")
    etiqueta, valor = documents.fila_total_pagado_prestamo(con_descuento)
    assert etiqueta == "Total cancelado"
    assert documents.gs("30000.00") in valor

    sin_descuento = SimpleNamespace(total_paid="240000.00", total_discount="0.00")
    assert documents.fila_total_pagado_prestamo(sin_descuento) == (
        "Total pagado",
        documents.gs("240000.00"),
    )


def test_el_historial_aclara_el_descuento_en_el_monto():
    entry = SimpleNamespace(amount="480000.00", discount_amount="30000.00")
    assert "desc." in payoff.history_amount_text(entry)
    assert "desc." not in payoff.history_amount_text(
        SimpleNamespace(amount="240000.00", discount_amount="")
    )


def test_el_ticket_html_destaca_lo_entregado_y_no_lo_imputado():
    """`ticket_cobro_html()` ya no es lo que se imprime (ver CLAUDE.md), pero
    sigue vivo y tiene que decir lo mismo que el ESC/POS."""
    from datetime import datetime

    from google.protobuf.timestamp_pb2 import Timestamp

    marca = Timestamp()
    marca.FromDatetime(datetime(2026, 9, 30, 12, 0))
    pago = _cobro_total(
        covered_installments=[1, 2],
        total_installments=2,
        recorded_by_name="Cajera",
        recorded_by_national_id="123",
        paid_at=marca,
        payment_method="EFECTIVO",
        transfer_reference="",
        total_paid="480000.00",
        remaining_balance="0.00",
        status="PAID",
    )
    loan = SimpleNamespace(id="abcdef12-0000")
    client = SimpleNamespace(
        first_name="Ana", last_name="Pérez", national_id="1", phone_number="0981"
    )

    html = documents.ticket_cobro_html(loan, client, pago)

    assert "TOTAL ABONADO" in html
    assert "Descuento por pago total" in html
    assert documents.gs("451064.00") in html


def test_el_ticket_termico_imprime_el_descuento_y_el_total_entregado():
    from datetime import datetime

    from google.protobuf.timestamp_pb2 import Timestamp

    marca = Timestamp()
    marca.FromDatetime(datetime(2026, 9, 30, 12, 0))
    pago = _cobro_total(
        covered_installments=[1, 2],
        total_installments=2,
        recorded_by_name="Cajera",
        recorded_by_national_id="123",
        paid_at=marca,
        payment_method="EFECTIVO",
        transfer_reference="",
        total_paid="480000.00",
        remaining_balance="0.00",
        status="PAID",
        payment_id="p-1",
    )
    loan = SimpleNamespace(id="abcdef12-0000")
    client = SimpleNamespace(
        first_name="Ana", last_name="Pérez", national_id="1", phone_number="0981"
    )

    texto = escpos.ticket_cobro_escpos(loan, client, pago).decode("cp437")

    assert "Descuento por pago total" in texto
    assert "451.064" in texto
    assert "Total cancelado" in texto
