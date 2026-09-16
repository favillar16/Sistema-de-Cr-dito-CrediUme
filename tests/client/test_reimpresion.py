"""Reimpresión de un cobro ya registrado -- BR-LOAN-016.

Antes de que existiera el historial de cobros, el comprobante sólo podía
emitirse mientras el pago seguía en memoria: cambiar de pantalla lo perdía y
un ticket que salía mal de la térmica no se podía repetir. Ahora el papel se
puede volver a emitir desde `ListLoanPayments`, y eso trae dos exigencias que
estos tests fijan:

  * el duplicado dice **las mismas cifras** que el original -- por eso el
    servidor devuelve la imputación y el saldo *de ese pago*, y no se
    recalculan acá;
  * el duplicado **se declara duplicado**. El número de ticket se deriva del
    préstamo y del instante del cobro, así que dos impresiones del mismo pago
    llevan el mismo número: sin la marca, dos papeles idénticos se leen como
    dos cobros distintos.
"""

from datetime import datetime, timezone

from google.protobuf.timestamp_pb2 import Timestamp

# Importar cas_client primero: su __init__ agrega el directorio del paquete al
# sys.path, que es donde viven los stubs generados (ver "Generated stub
# imports" en CLAUDE.md -- protoc los genera con imports planos).
import cas_client  # noqa: F401
import loan_service_pb2
from cas_client import documents
from tests.client.test_documents_identity import _FakeClient, _FakeLoanCompleto


def _entrada(
    *,
    monto: str = "1768056.00",
    cuotas=(3,),
    saldo_posterior: str = "12000000.00",
    medio: str = "EFECTIVO",
    referencia: str = "",
    operador: str = "Ana Giménez",
) -> loan_service_pb2.LoanPaymentEntry:
    """Una fila del historial tal como la devuelve el servidor."""
    momento = Timestamp()
    momento.FromDatetime(datetime(2026, 9, 14, 15, 30, tzinfo=timezone.utc))
    return loan_service_pb2.LoanPaymentEntry(
        id="9f1d0b6e-0000-4000-8000-000000000001",
        amount=monto,
        paid_at=momento,
        payment_method=medio,
        transfer_reference=referencia,
        covered_installments=list(cuotas),
        total_paid_after="5304168.00",
        remaining_balance_after=saldo_posterior,
        recorded_by_name=operador,
        recorded_by_national_id="4111222",
    )


def _cobro_fresco() -> loan_service_pb2.RecordPaymentResponse:
    """El cobro recién registrado, que NO es una reimpresión."""
    momento = Timestamp()
    momento.FromDatetime(datetime(2026, 9, 14, 15, 30, tzinfo=timezone.utc))
    return loan_service_pb2.RecordPaymentResponse(
        success=True,
        status="ACTIVE",
        total_paid="5304168.00",
        remaining_balance="12000000.00",
        covered_installments=[3],
        total_installments=18,
        amount_paid="1768056.00",
        paid_at=momento,
        payment_method="EFECTIVO",
        recorded_by_name="Ana Giménez",
        recorded_by_national_id="4111222",
    )


def test_el_cobro_historico_se_lee_como_el_recien_registrado():
    """Las dos fuentes traen los mismos datos con distinto nombre; el
    adaptador existe para que haya UNA plantilla de cada papel."""
    historico = documents.CobroHistorico(_entrada(), total_installments=18)
    fresco = _cobro_fresco()

    assert historico.amount_paid == fresco.amount_paid
    assert list(historico.covered_installments) == list(fresco.covered_installments)
    assert historico.total_installments == fresco.total_installments
    assert historico.total_paid == fresco.total_paid
    assert historico.remaining_balance == fresco.remaining_balance
    assert historico.recorded_by_name == fresco.recorded_by_name
    assert historico.payment_method == fresco.payment_method


def test_un_cobro_que_dejo_el_saldo_en_cero_cancela_el_prestamo():
    """`status` no viaja en el historial: se deduce del saldo posterior, que es
    la misma condición con la que RecordPayment pone el préstamo en PAID."""
    saldado = documents.CobroHistorico(
        _entrada(saldo_posterior="0.00"), total_installments=18
    )
    pendiente = documents.CobroHistorico(_entrada(), total_installments=18)

    assert saldado.status == "PAID"
    assert pendiente.status == "ACTIVE"


def test_el_ticket_reimpreso_dice_que_es_una_reimpresion():
    pago = documents.CobroHistorico(_entrada(), total_installments=18)

    html = documents.ticket_cobro_html(_FakeLoanCompleto, _FakeClient, pago)

    assert "REIMPRESI&Oacute;N" in html


def test_el_ticket_del_cobro_recien_hecho_no_lleva_la_marca():
    html = documents.ticket_cobro_html(_FakeLoanCompleto, _FakeClient, _cobro_fresco())

    assert "REIMPRESI&Oacute;N" not in html


def test_el_duplicado_conserva_el_numero_del_ticket_original():
    """El número identifica el COBRO, no la impresión: si cambiara, el
    duplicado no se podría casar con el original en el arqueo."""
    original = _cobro_fresco()
    duplicado = documents.CobroHistorico(_entrada(), total_installments=18)

    assert documents.numero_ticket(
        _FakeLoanCompleto, duplicado
    ) == documents.numero_ticket(_FakeLoanCompleto, original)


def test_el_duplicado_repite_las_cifras_del_original():
    """Lo que no puede pasar es que el papel reimpreso diga otro monto, otra
    cuota u otro saldo que el que se entregó en su momento."""
    original = documents.ticket_cobro_html(
        _FakeLoanCompleto, _FakeClient, _cobro_fresco()
    )
    duplicado = documents.ticket_cobro_html(
        _FakeLoanCompleto,
        _FakeClient,
        documents.CobroHistorico(_entrada(), total_installments=18),
    )

    for dato in ("1.768.056 Gs", "12.000.000 Gs", "Cuota(s) 3 de 18", "Ana Giménez"):
        assert dato in original
        assert dato in duplicado


def test_el_comprobante_a4_reimpreso_tambien_se_declara_duplicado():
    pago = documents.CobroHistorico(_entrada(), total_installments=18)

    html = documents.comprobante_pago_html(_FakeLoanCompleto, _FakeClient, pago)

    assert "REIMPRESI&Oacute;N" in html
    assert "duplicado de un comprobante ya emitido" in html


def test_el_comprobante_a4_y_el_ticket_citan_el_mismo_numero():
    """Los dos papeles documentan el mismo cobro, así que tienen que poder
    casarse entre sí (y con el arqueo). El A4 no llevaba número propio."""
    pago = _cobro_fresco()
    numero = documents.numero_ticket(_FakeLoanCompleto, pago)

    a4 = documents.comprobante_pago_html(_FakeLoanCompleto, _FakeClient, pago)
    ticket = documents.ticket_cobro_html(_FakeLoanCompleto, _FakeClient, pago)

    assert numero in a4
    assert numero in ticket


def test_el_comprobante_del_cobro_recien_hecho_no_se_declara_duplicado():
    html = documents.comprobante_pago_html(
        _FakeLoanCompleto, _FakeClient, _cobro_fresco()
    )

    assert "REIMPRESI&Oacute;N" not in html


def test_un_cobro_por_transferencia_reimprime_su_referencia():
    """BR-CAJA-004: el medio y la referencia son parte de lo que el papel
    certifica, así que tienen que sobrevivir a la reimpresión."""
    pago = documents.CobroHistorico(
        _entrada(medio="TRANSFERENCIA", referencia="TRX-99812"),
        total_installments=18,
    )

    html = documents.ticket_cobro_html(_FakeLoanCompleto, _FakeClient, pago)

    assert "TRX-99812" in html


def test_un_cobro_anterior_a_la_columna_no_inventa_un_cajero():
    """Los pagos registrados antes de BR-LOAN-016 no guardaron quién cobró:
    el papel lo dice en vez de atribuirle el cobro a quien reimprime."""
    pago = documents.CobroHistorico(_entrada(operador=""), total_installments=18)

    html = documents.ticket_cobro_html(_FakeLoanCompleto, _FakeClient, pago)

    assert "Ana Giménez" not in html
    assert "No registrado" in html
