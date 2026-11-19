"""BR-LOAN-019: el aviso que se le muestra al operador antes de borrar un
cobro. Es lo único que le dice qué pasa con la caja y con los cobros
posteriores, así que se fija acá."""

from datetime import datetime, timezone

import loan_service_pb2
from google.protobuf.timestamp_pb2 import Timestamp

from cas_client.payment_deletion import confirmation_text, success_message


def _entry(medio="TRANSFERENCIA"):
    momento = Timestamp()
    momento.FromDatetime(datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc))
    return loan_service_pb2.LoanPaymentEntry(
        id="p1",
        amount="150000.00",
        total_charged="152000.00",
        paid_at=momento,
        payment_method=medio,
        covered_installments=[2],
    )


def test_names_the_amount_method_and_installments():
    texto = confirmation_text(_entry(), 12, is_latest=True)
    assert "152.000 Gs" in texto
    assert "Transferencia" in texto
    assert "2 de 12" in texto
    assert "no se puede deshacer" in texto


def test_cash_warns_about_the_till():
    assert "caja" in confirmation_text(_entry("EFECTIVO"), 12, is_latest=True)
    assert "caja" not in confirmation_text(_entry(), 12, is_latest=True)


def test_an_older_payment_warns_about_reimputation():
    assert "posteriores" in confirmation_text(_entry(), 12, is_latest=False)
    assert "posteriores" not in confirmation_text(_entry(), 12, is_latest=True)


def test_success_message_reports_cash_removed():
    con_caja = loan_service_pb2.DeleteLoanPaymentResponse(cash_amount_removed="5000.00")
    assert "5.000 Gs" in success_message(con_caja)
    assert success_message(loan_service_pb2.DeleteLoanPaymentResponse()) == (
        "Cobro eliminado."
    )
