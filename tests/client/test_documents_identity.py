"""Guarda de la identidad legal y de las condiciones comerciales impresas en
los documentos generados.

El nombre/RUC/dirección/celular de la entidad son datos reales de inscripción
ante la DNIT: si alguien los revierte a los placeholders anteriores
("CREDIUME S.A.", "80XXXXXXX-X") los documentos salen a la calle con datos de
otra denominación.

Desde que la entidad autorizó el texto legal, estos tests cubren además las
condiciones que el Pagaré y el Contrato declaran (interés compensatorio y
moratorio, plazo de gracia de la mora, umbral de vencimiento anticipado y
jurisdicción). Ya no son placeholders: se imprimen en un instrumento que firma
el cliente, así que un cambio silencioso acá cambia lo que la entidad puede
cobrar y ejecutar.

Sin dependencia de Qt: documents.py solo arma HTML como string.
"""

import re
from html import unescape

from cas_client import documents


def test_company_identity_matches_the_dnit_registration():
    assert documents._COMPANY_NAME == "CREDIMED UME"
    assert documents._COMPANY_RUC == "1276703-4"
    assert "Ayolas c/ Acaray" in documents._COMPANY_ADDRESS
    assert "Coronel Oviedo" in documents._COMPANY_ADDRESS
    assert documents._COMPANY_PHONE == "(0984) 319243"


def test_no_document_still_carries_the_previous_denomination():
    """Cubre las 4 plantillas de préstamo más el reporte de período."""
    for html in (
        documents._header("Cualquiera"),
        documents.reporte_periodo_html(_FakeReport()),
    ):
        assert "CREDIUME" not in html.upper().replace("CREDIMED UME", "")
        assert "80XXXXXXX-X" not in html


def test_header_prints_ruc_address_and_phone():
    html = documents._header("Liquidación de Préstamo")
    assert documents._COMPANY_RUC in html
    assert documents._COMPANY_ADDRESS in html
    assert documents._COMPANY_PHONE in html


def test_expired_loans_read_as_rechazado_not_caducado():
    """Cambio de terminología pedido por producto -- el valor del enum del
    servidor sigue siendo EXPIRED."""
    assert documents._ESTADOS_LABEL["EXPIRED"] == "Rechazado"
    assert "Caducado" not in documents._ESTADOS_LABEL.values()


class _FakeReport:
    """Mínimo GetPeriodReportResponse-like para renderizar la plantilla."""

    start_date = "2026-08-01"
    end_date = "2026-08-31"
    clients_registered = 3
    loans_created = 5
    loans_approved = 4
    principal_created = "15000000.00"
    principal_approved = "12000000.00"
    payments_count = 9
    payments_total = "4500000.00"
    loans_paid = 1
    active_loans_at_close = 7
    outstanding_at_close = "30000000.00"
    overdue_at_close = "1200000.00"
    overdue_loans_at_close = 2


def test_period_report_renders_dates_as_day_month_year():
    html = documents.reporte_periodo_html(_FakeReport())
    assert "01/08/2026" in html
    assert "31/08/2026" in html
    assert "2026-08-01" not in html


def test_period_report_shows_the_overdue_total():
    html = documents.reporte_periodo_html(_FakeReport())
    assert "Monto total de mora" in html
    assert "1.200.000 Gs" in html


def test_period_report_has_no_draft_banner():
    """Igual que el Cronograma: son cifras calculadas, no texto legal."""
    html = documents.reporte_periodo_html(_FakeReport())
    assert "BORRADOR" not in html


# ---- Condiciones comerciales autorizadas (Pagaré / Contrato) --------------


class _FakeLoanCompleto:
    """Lo mínimo que pagare_html()/contrato_html() leen del préstamo."""

    id = "91cc3960-1111-2222-3333-444455556666"
    status = "ACTIVE"
    principal_amount = "18000000.00"
    interest_rate = "0.18"  # tasa fija vigente: 18% anual = 1,5% mensual
    term_months = 18
    first_due_date = "2026-09-15"
    total_paid = "0.00"
    remaining_balance = "18000000.00"
    guarantee_type = ""
    guarantee_amount = "0.00"
    charge_interest_tax = ""
    charge_admin_fee = ""
    charge_cancellation_insurance = ""
    charge_contracted_insurance = ""
    total_charges = "0.00"
    total_credit_with_charges = "18000000.00"


def _texto_plano(documento: str) -> str:
    """Texto tal como lo lee el cliente en el documento renderizado.

    Las plantillas cortan las frases con saltos de línea e indentación y
    escapan los acentos como entidades ("facultar&aacute;"); QTextDocument
    colapsa ambas cosas al imprimir. Estos tests afirman sobre las condiciones
    que el cliente firma, no sobre en qué columna quedó cortado el f-string,
    así que normalizan primero -- si no, reformatear una cláusula sin cambiar
    una sola palabra rompería la guarda."""
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", documento)).split())


def test_authorised_commercial_terms():
    """Las condiciones que autorizó la entidad. Cambiarlas cambia el
    instrumento legal, no solo la redacción."""
    assert documents._TERM_MORATORY_RATE == "0,38% mensual"
    assert documents._TERM_MORATORY_GRACE_DAYS == 11
    assert documents._TERM_ACCELERATION_INSTALLMENTS == 4
    assert documents._TERM_JURISDICTION_CITY == "Coronel Oviedo"


def test_compensatory_interest_is_quoted_monthly_not_annually():
    """El sistema guarda la tasa nominal anual (0.18) pero la cláusula la
    declara mensual (1,5%), que es lo que amortization.py cobra por período.
    Imprimir "18%" ahí prometería una tasa distinta de la cuota calculada."""
    for documento in (
        documents.pagare_html(_FakeLoanCompleto, _FakeClient),
        documents.contrato_html(_FakeLoanCompleto, _FakeClient),
    ):
        texto = _texto_plano(documento)
        assert "compensatorio del 1,5% mensual sobre saldos deudores" in texto
        assert "18% mensual" not in texto


def test_pagare_and_contrato_print_the_mora_terms():
    for documento in (
        documents.pagare_html(_FakeLoanCompleto, _FakeClient),
        documents.contrato_html(_FakeLoanCompleto, _FakeClient),
    ):
        texto = _texto_plano(documento)
        assert "punitorio del 0,38% mensual" in texto
        # La mora recién se devenga a los 11 días del primer vencimiento.
        assert "a partir de los 11 (once) días corridos" in texto
        assert "4 (cuatro) cuotas vencidas" in texto
        assert "Tribunales de Coronel Oviedo" in texto


def test_acceleration_is_a_faculty_of_the_lender_not_automatic():
    """La condición autorizada dice "se podrá proceder a exigir el total
    adeudado" -- una facultad de la entidad. El texto anterior lo hacía
    automático, que es una obligación distinta."""
    texto = _texto_plano(documents.pagare_html(_FakeLoanCompleto, _FakeClient))
    assert "facultará a CREDIMED UME a exigir el total adeudado" in texto
    assert "automáticamente exigible" not in texto


def test_no_placeholder_survives_in_the_signed_instruments():
    """Un documento que va a la firma no puede salir con "[A DEFINIR]"."""
    for html in (
        documents.pagare_html(_FakeLoanCompleto, _FakeClient),
        documents.contrato_html(_FakeLoanCompleto, _FakeClient),
        documents.liquidacion_html(_FakeLoanCompleto, _FakeClient, _FakeSchedule),
    ):
        assert "A DEFINIR" not in html
        assert "[N]" not in html
        assert "BORRADOR" not in html


class _FakeSchedule:
    installments: list = []


# ---- BR-LOAN-011: comprobante de pago -------------------------------------


def test_cuotas_cubiertas_texto_single_installment():
    """El formato exacto pedido: "Cuota(s) 1 de 18"."""
    assert documents.cuotas_cubiertas_texto([1], 18) == "Cuota(s) 1 de 18"


def test_cuotas_cubiertas_texto_multiple_installments():
    """Y el caso de más de una: "Cuota(s) 1,2 de 18"."""
    assert documents.cuotas_cubiertas_texto([1, 2], 18) == "Cuota(s) 1,2 de 18"
    assert documents.cuotas_cubiertas_texto([7, 8, 9], 24) == "Cuota(s) 7,8,9 de 24"


def test_cuotas_cubiertas_texto_without_installments_does_not_crash():
    """Un comprobante no es lugar para reventar por una lista vacía."""
    assert documents.cuotas_cubiertas_texto([], 18) == "Cuota(s) — de 18"


def test_responsable_prints_name_and_national_id():
    assert (
        documents.responsable("Ana Benítez", "4123456") == "Ana Benítez (C.I. 4123456)"
    )


def test_responsable_degrades_by_parts_not_all_or_nothing():
    """Los usuarios anteriores a BR-AUTH-006 no tienen datos personales: el
    documento imprime lo que haya antes que dejar el campo en blanco."""
    assert documents.responsable("Ana Benítez", "") == "Ana Benítez"
    assert documents.responsable("", "", respaldo="ana.b") == "ana.b"
    assert documents.responsable("", "") == "No registrado"


class _FakePayment:
    status = "ACTIVE"
    covered_installments = [1, 2]
    total_installments = 18
    amount_paid = "1800000.00"
    total_paid = "1800000.00"
    remaining_balance = "16200000.00"
    transfer_reference = "TRF-99887"
    # BR-CAJA-004. El desglose por medio de pago tiene su propio archivo
    # (test_documents_payment_method.py); acá solo hace falta que el campo
    # exista para poder renderizar el comprobante.
    payment_method = "TRANSFERENCIA"
    recorded_by_name = "Ana Benítez"
    recorded_by_national_id = "4123456"

    class paid_at:
        @staticmethod
        def ToDatetime():
            from datetime import datetime

            return datetime(2026, 8, 13, 14, 30)


class _FakeLoan:
    id = "91cc3960-1111-2222-3333-444455556666"


class _FakeClient:
    first_name = "Fabrizio"
    last_name = "Villar"
    national_id = "5746680"
    address = "Barrio San Miguel"
    phone_number = "0984992634"


def test_comprobante_shows_amount_installments_and_operator():
    html = documents.comprobante_pago_html(_FakeLoan, _FakeClient, _FakePayment)
    assert "1.800.000 Gs" in html  # monto abonado
    assert "Cuota(s) 1,2 de 18" in html  # cuotas que corresponden
    assert "Ana Benítez (C.I. 4123456)" in html
    assert "TRF-99887" in html
    assert "13/08/2026 14:30" in html  # fecha en DD/MM/AAAA


def test_comprobante_has_no_draft_banner():
    """No tiene texto legal: son cifras de un pago ya registrado."""
    html = documents.comprobante_pago_html(_FakeLoan, _FakeClient, _FakePayment)
    assert "BORRADOR" not in html


def test_comprobante_announces_a_fully_repaid_loan():
    class Saldado(_FakePayment):
        status = "PAID"
        remaining_balance = "0.00"

    html = documents.comprobante_pago_html(_FakeLoan, _FakeClient, Saldado)
    assert "totalmente cancelado" in html
    assert "totalmente cancelado" not in documents.comprobante_pago_html(
        _FakeLoan, _FakeClient, _FakePayment
    )
