"""BR-DASH-003: cómo el reporte de Estado de Pago de Clientes presenta cada
fila.

Se arma la respuesta real (dashboard_service_pb2) en vez de un stub: el punto
del reporte es que la pantalla, el PDF y el DOCX salgan de una única
definición de filas, así que conviene ejercitar el mensaje que efectivamente
viaja. Sin Qt, sin gRPC y sin base, igual que el resto de tests/client/.
"""

import re
from datetime import datetime, timezone
from html import unescape

# cas_client primero a propósito: su __init__ es el que agrega el paquete al
# sys.path para que los stubs generados (que se importan planos, sin paquete --
# ver CLAUDE.md) resuelvan.
from cas_client import documents

import dashboard_service_pb2  # noqa: E402  (ver el comentario de arriba)


def _reporte(*filas, only_overdue=False):
    generado = dashboard_service_pb2.GetClientPaymentStatusReportResponse(
        only_overdue=only_overdue,
        rows=list(filas),
        clients_count=len(filas),
        overdue_clients_count=sum(
            1 for fila in filas if fila.payment_status != "AL_DIA"
        ),
        total_outstanding="0.00",
        total_overdue="0.00",
    )
    generado.generated_at.FromDatetime(
        datetime(2026, 8, 26, 15, 30, tzinfo=timezone.utc)
    )
    return generado


def _fila(**kwargs):
    base = dict(
        client_id="11111111-2222-3333-4444-555555555555",
        client_name="Ana Gómez",
        national_id="1234567",
        phone_number="0981000000",
        active_loans_count=1,
        defaulted_loans_count=0,
        outstanding_balance="1500000.00",
        overdue_amount="0.00",
        overdue_installments_count=0,
        next_due_date="2026-09-15",
        next_due_amount="450000.00",
        payment_status="AL_DIA",
    )
    base.update(kwargs)
    return dashboard_service_pb2.ClientPaymentStatusRow(**base)


def _texto_plano(documento: str) -> str:
    """Texto tal como lo lee el operador en el documento renderizado -- misma
    normalización que test_documents_identity.py."""
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", documento)).split())


def test_row_columns_match_the_declared_headers():
    """La tabla de la vista, el PDF y el DOCX se construyen todos sobre
    ESTADO_PAGOS_COLUMNAS: si una fila tuviera otra cantidad de celdas, las
    tres saldrían corridas."""
    (fila,) = documents._filas_estado_pagos(_reporte(_fila()))

    assert len(fila) == len(documents.ESTADO_PAGOS_COLUMNAS)


def test_dates_and_amounts_are_shown_in_the_display_format():
    """El reporte lo lee una persona: las fechas en DD/MM/AAAA y los importes
    en guaraníes, no el formato de cable."""
    (fila,) = documents._filas_estado_pagos(_reporte(_fila()))

    assert fila[0] == "Ana Gómez"
    assert "15/09/2026" in fila[4]
    assert "2026-09-15" not in fila[4]
    assert fila[5] == "1.500.000 Gs"


def test_a_client_without_a_next_installment_shows_a_dash_not_an_empty_cell():
    """next_due_date llega vacío cuando no queda cuota impaga (p. ej. solo
    tiene préstamos incumplidos). Una celda en blanco se leería como un dato
    que se perdió."""
    (fila,) = documents._filas_estado_pagos(
        _reporte(_fila(next_due_date="", next_due_amount=""))
    )

    assert fila[4].strip() == "—"


def test_overdue_row_reports_how_many_installments_are_behind():
    (fila,) = documents._filas_estado_pagos(
        _reporte(
            _fila(
                overdue_amount="900000.00",
                overdue_installments_count=2,
                payment_status="CUOTA_VENCIDA",
            )
        )
    )

    assert "900.000 Gs" in fila[6]
    assert "2 cuota/s" in fila[6]
    assert fila[7] == "Cuota vencida"


def test_defaulted_loans_are_visible_in_the_loans_cell():
    """Un incumplido no es "un préstamo activo más": si no se dijera, la fila
    mostraría 1 préstamo cuando el cliente tiene dos situaciones distintas."""
    (fila,) = documents._filas_estado_pagos(
        _reporte(_fila(defaulted_loans_count=1, payment_status="INCUMPLIDO"))
    )

    assert "1 incumplido(s)" in fila[3]
    assert fila[7] == "Incumplido"


def test_html_states_the_scope_of_the_filter_applied():
    """El mismo documento con y sin filtro tiene totales distintos; si no
    dijera cuál se aplicó, dos impresiones serían indistinguibles."""
    con_filtro = _texto_plano(
        documents.reporte_estado_pagos_html(
            _reporte(_fila(), only_overdue=True), "operador"
        )
    )
    sin_filtro = _texto_plano(
        documents.reporte_estado_pagos_html(
            _reporte(_fila(), only_overdue=False), "operador"
        )
    )

    assert "Solo clientes con cuotas vencidas" in con_filtro
    assert "Todos los clientes con cartera viva" in sin_filtro


def test_html_says_the_totals_cover_the_listed_clients_only():
    """Con only_overdue los totales describen el subconjunto, no la cartera:
    leerlos como el total de la empresa sería un error de gestión."""
    texto = _texto_plano(
        documents.reporte_estado_pagos_html(_reporte(_fila()), "operador")
    )

    assert "no a toda la cartera" in texto


def test_empty_report_says_so_instead_of_printing_an_empty_table():
    texto = _texto_plano(
        documents.reporte_estado_pagos_html(_reporte(only_overdue=True), "operador")
    )

    assert "Sin clientes que informar" in texto


def test_html_prints_the_generation_time_in_local_time():
    """generated_at viaja como Timestamp (naive UTC al desempaquetarse); el
    reporte tiene que decir la hora del operador, misma regla que el
    Comprobante de Pago."""
    texto = _texto_plano(
        documents.reporte_estado_pagos_html(_reporte(_fila()), "operador")
    )

    assert "26/08/2026" in texto
