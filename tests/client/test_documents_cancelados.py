"""BR-DASH-004: cómo el listado de préstamos cancelados presenta cada fila.

Mismo criterio que test_documents_payment_status.py: se arma el mensaje real
(dashboard_service_pb2), porque la pantalla, el PDF y el DOCX salen de una
única definición de filas (documents._filas_cancelados).
"""

from datetime import datetime, timezone

# cas_client primero: su __init__ agrega el paquete al sys.path para que los
# stubs generados resuelvan (ver CLAUDE.md).
from cas_client import documents, documents_docx

import dashboard_service_pb2  # noqa: E402


def _fila(**kwargs):
    base = dict(
        client_id="c1",
        client_name="Ana Pérez",
        national_id="4512330",
        phone_number="0981000000",
        loan_id="3fa85f64-5717-4562-b3fc-2c963f66afa6",
        principal_amount="1000000.00",
        term_months=12,
        total_collected="1200000.00",
        total_discount="0.00",
        total_late_fee="0.00",
    )
    base.update(kwargs)
    fila = dashboard_service_pb2.PaidLoanRow(**base)
    fila.paid_off_at.FromDatetime(datetime(2026, 9, 15, 14, 0, tzinfo=timezone.utc))
    return fila


def _reporte(*filas, total_discount="0.00"):
    reporte = dashboard_service_pb2.GetPaidLoansReportResponse(
        rows=list(filas),
        clients_count=len({f.client_id for f in filas}),
        loans_count=len(filas),
        total_principal="1000000.00",
        total_collected="1200000.00",
        total_discount=total_discount,
    )
    reporte.generated_at.FromDatetime(datetime(2026, 10, 7, 15, 0, tzinfo=timezone.utc))
    return reporte


def test_una_fila_tiene_todas_las_columnas():
    (fila,) = documents._filas_cancelados(_reporte(_fila()))

    assert len(fila) == len(documents.CANCELADOS_COLUMNAS)
    assert fila[0] == "Ana Pérez"
    assert fila[3] == "3fa85f64"
    assert fila[4] == "1.000.000 Gs"
    assert fila[6] == "15/09/2026"
    assert fila[7] == "1.200.000 Gs"


def test_el_descuento_se_aclara_junto_al_total_cobrado():
    (fila,) = documents._filas_cancelados(
        _reporte(_fila(total_collected="900000.00", total_discount="100000.00"))
    )
    assert fila[7] == "900.000 Gs (desc. 100.000 Gs)"


def test_sin_fecha_de_cancelacion_se_muestra_un_guion():
    sin_fecha = dashboard_service_pb2.PaidLoanRow(
        client_name="X", loan_id="abcdef0123", total_collected="0.00"
    )
    (fila,) = documents._filas_cancelados(_reporte(sin_fecha))
    assert fila[6] == "—"


def test_pdf_y_docx_salen_de_las_mismas_filas():
    reporte = _reporte(_fila(), total_discount="100000.00")

    html = documents.reporte_cancelados_html(reporte, "tester")
    assert "Ana P" in html and "1.200.000 Gs" in html
    assert "Descuentos otorgados" in html

    docx = documents_docx.reporte_cancelados_docx(reporte, "tester")
    celdas = [c.text for c in docx.tables[-1].rows[1].cells]
    assert celdas == list(documents._filas_cancelados(reporte)[0])
