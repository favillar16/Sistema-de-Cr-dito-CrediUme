"""BR-CAJA-003: el arqueo de caja para firmar y archivar (open item de
CLAUDE.md -- "no printable arqueo document").

Se arma sobre el mensaje real (cash_service_pb2.CashSessionDetail), no un
stub a mano, por el mismo motivo que test_documents_payment_status.py: el
punto es que la pantalla y el documento describan el mismo CashSessionDetail,
así que conviene ejercitar el mensaje que efectivamente viaja. Sin Qt, sin
gRPC y sin base, igual que el resto de tests/client/.
"""

import re
from datetime import datetime, timezone
from html import unescape

from cas_client import documents, documents_docx, theme

import cash_service_pb2  # noqa: E402  (ver el comentario de arriba)


def _movimiento(**kwargs):
    base = dict(
        id="m1",
        movement_type="INGRESO",
        amount="500000.00",
        concept="Reposición de fondo",
        loan_payment_id="",
        is_automatic=False,
    )
    base.update(kwargs)
    movimiento = cash_service_pb2.CashMovementEntry(**base)
    movimiento.created_at.FromDatetime(datetime(2026, 9, 18, 9, 5, tzinfo=timezone.utc))
    return movimiento


def _detalle(**kwargs):
    base = dict(
        id="s1",
        cashier_username="mgonzalez",
        cashier_full_name="Marta González",
        status="CLOSED",
        opening_amount="500000.00",
        opening_notes="",
        total_income="1800000.00",
        total_expense="300000.00",
        total_loan_collections="1500000.00",
        expected_amount="2000000.00",
        movements_count=1,
        closing_counted_amount="1990000.00",
        closing_expected_amount="2000000.00",
        closing_difference="-10000.00",
        closing_notes="",
        closed_by_username="mgonzalez",
    )
    base.update(kwargs)
    movimientos = base.pop("movements", [_movimiento()])
    detalle = cash_service_pb2.CashSessionDetail(movements=movimientos, **base)
    detalle.opened_at.FromDatetime(datetime(2026, 9, 18, 8, 0, tzinfo=timezone.utc))
    detalle.closed_at.FromDatetime(datetime(2026, 9, 18, 18, 0, tzinfo=timezone.utc))
    return detalle


def _texto_plano(documento: str) -> str:
    """Texto tal como lo lee el operador -- misma normalización que
    test_documents_identity.py."""
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", documento)).split())


def test_identifica_el_turno_y_al_cajero():
    texto = _texto_plano(documents.reporte_arqueo_html(_detalle()))
    assert "Marta González" in texto
    assert "Cerrada" in texto


def test_trae_los_totales_del_turno():
    texto = _texto_plano(documents.reporte_arqueo_html(_detalle()))
    assert "500.000 Gs" in texto  # monto inicial
    assert "1.800.000 Gs" in texto  # ingresos
    assert "300.000 Gs" in texto  # egresos
    assert "1.500.000 Gs" in texto  # cobros de cuotas
    assert "2.000.000 Gs" in texto  # esperado
    assert "1.990.000 Gs" in texto  # contado


def test_un_faltante_sale_en_rojo_y_con_signo():
    html = documents.reporte_arqueo_html(_detalle(closing_difference="-10000.00"))
    assert "-10.000 Gs" in _texto_plano(html)
    assert theme.ERROR in html


def test_un_sobrante_sale_positivo():
    texto = _texto_plano(
        documents.reporte_arqueo_html(_detalle(closing_difference="5000.00"))
    )
    assert "5.000 Gs" in texto
    assert "-5.000 Gs" not in texto


def test_un_arqueo_exacto_sale_en_verde():
    html = documents.reporte_arqueo_html(_detalle(closing_difference="0.00"))
    assert theme.SUCCESS in html


def test_lista_los_movimientos_del_turno():
    texto = _texto_plano(documents.reporte_arqueo_html(_detalle()))
    assert "Reposición de fondo" in texto
    assert "Ingreso" in texto


def test_sin_movimientos_lo_dice_en_vez_de_una_tabla_vacia():
    texto = _texto_plano(documents.reporte_arqueo_html(_detalle(movements=[])))
    assert "Sin movimientos registrados en este turno" in texto


def test_marca_cuando_un_supervisor_cerro_la_caja_de_otro_cajero():
    """BR-CAJA-003: sólo tiene sentido mostrar "Cerrado por" cuando no
    coincide con el cajero -- si cerró su propia caja, ya se lo identifica
    como "Cajero" arriba."""
    propio = _texto_plano(documents.reporte_arqueo_html(_detalle()))
    assert "Cerrado por" not in propio

    otro = _texto_plano(
        documents.reporte_arqueo_html(_detalle(closed_by_username="gerente1"))
    )
    assert "Cerrado por" in otro
    assert "gerente1" in otro


def test_deja_espacio_de_firma_para_el_cajero_y_el_supervisor():
    texto = _texto_plano(documents.reporte_arqueo_html(_detalle()))
    assert "FIRMA DEL CAJERO" in texto
    assert "FIRMA DEL SUPERVISOR" in texto


def test_no_se_entrega_al_cliente_y_lo_dice():
    texto = _texto_plano(documents.reporte_arqueo_html(_detalle()))
    assert "no se entrega al cliente" in texto


def test_el_docx_trae_el_mismo_contenido_que_el_pdf():
    """Mismo criterio que test_ficha_cliente.py: el DOCX no puede describir
    un cierre distinto del que ya vio el cajero en el PDF/pantalla."""
    detalle = _detalle()
    documento = documents_docx.reporte_arqueo_docx(detalle)

    texto = "\n".join(p.text for p in documento.paragraphs)
    for tabla in documento.tables:
        for fila in tabla.rows:
            texto += "\n" + "\n".join(celda.text for celda in fila.cells)

    assert "Marta González" in texto
    assert "Reposición de fondo" in texto
    assert "FIRMA DEL CAJERO" in texto
    assert "FIRMA DEL SUPERVISOR" in texto
