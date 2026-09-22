"""El monto que se ve antes de confirmar es el que se va a cobrar.

BR-LOAN-017 hizo que el servidor cobre **cuota + mora**. Si la pantalla
muestra sólo la cuota, el operador confirma un número y se registra otro --
la incongruencia que hay que impedir, más grave todavía en efectivo, donde
ese total es lo que tiene que aparecer en el arqueo.

Hay **dos** entradas de cobro, con la misma regla y distinta pantalla: la
tarjeta de la caja (el cajero, `CashView`) y la del detalle del préstamo (el
resto de los roles, `LoansView`). Las dos se prueban acá juntas, porque el
defecto que motivó este archivo fue justamente arreglar una y olvidar la
otra.

Estas pruebas recorren el camino real del widget -- poblar el combo de cuotas
y leer lo que queda en el campo de monto -- y no una función suelta. Es lo que
faltaba: el desempaque del `itemData` del combo cambió de dos a tres valores
y ningún test lo caminaba, así que "Cobrar" reventaba con ValueError sin que
nada lo dijera.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from cas_client.session import Session
from cas_client.views.cash_view import CashView
from cas_client.views.loans_view import LoansView


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _monto(campo) -> Decimal:
    """El importe que muestra un CurrencyInput.

    Como Decimal y no como string: `raw_value()` normaliza ("240000", no
    "240000.00") y comparar texto haría fallar la prueba por el formato en
    vez de por la plata.
    """
    return Decimal(campo.raw_value())


class _StubClient:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


def _cuota(numero, monto, mora, pagada=False):
    """Una fila de GetAmortizationScheduleResponse."""
    return SimpleNamespace(
        installment_number=numero,
        due_date="2026-08-10",
        amount_due=monto,
        late_fee=mora,
        late_fee_days=35 if mora != "0.00" else 0,
        is_paid=pagada,
    )


_CRONOGRAMA = SimpleNamespace(
    installments=[
        _cuota(1, "240000.00", "1064.00"),
        _cuota(2, "240000.00", "0.00"),
    ]
)


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


# ---- Caja ------------------------------------------------------------------


def test_la_caja_cobra_cuota_mas_mora(caja):
    caja._on_schedule_loaded(_CRONOGRAMA)

    assert _monto(caja._collection_amount) == Decimal("241064.00")
    # isHidden() y no isVisibleTo(): la tarjeta de cobro entera está
    # oculta mientras no haya caja abierta, así que preguntar por la
    # visibilidad efectiva mediría eso y no el desglose.
    assert not caja._collection_breakdown.isHidden()
    assert "mora" in caja._collection_breakdown.text().lower()
    assert "+ mora" in caja._installment_combo.itemText(0)


def test_la_caja_no_menciona_la_mora_cuando_no_hay(caja):
    caja._on_schedule_loaded(
        SimpleNamespace(installments=[_cuota(2, "240000.00", "0.00")])
    )

    assert _monto(caja._collection_amount) == Decimal("240000.00")
    assert caja._collection_breakdown.isHidden()
    assert "mora" not in caja._installment_combo.itemText(0).lower()


def test_la_caja_manda_a_cobrar_la_cuota_elegida(caja):
    """Camina `_on_collect` de punta a punta con el combo poblado.

    Es la prueba que faltaba: acá se desempaqueta el `itemData` del combo, y
    cuando la mora se le agregó como tercer elemento este camino quedó
    rompiéndose con ValueError -- sin que ningún test lo notara, porque
    ninguno apretaba el botón.
    """
    caja._on_schedule_loaded(_CRONOGRAMA)
    caja._method_combo.setCurrentIndex(
        [
            caja._method_combo.itemData(i) for i in range(caja._method_combo.count())
        ].index("EFECTIVO")
    )
    enviado = {}

    def _capturar(fn, *args, on_success, **kwargs):
        enviado["args"] = args
        enviado["kwargs"] = kwargs

    caja._run_collection = _capturar
    caja._on_collect()

    assert enviado["kwargs"]["installment_number"] == 1
    assert enviado["kwargs"]["payment_method"] == "EFECTIVO"


# ---- Detalle del préstamo --------------------------------------------------


def test_el_detalle_del_prestamo_cobra_cuota_mas_mora(prestamos):
    prestamos._on_pending_installments_loaded(_CRONOGRAMA)

    assert _monto(prestamos._payment_amount_display) == Decimal("241064.00")
    assert not prestamos._payment_breakdown.isHidden()
    assert "+ mora" in prestamos._payment_installment_combo.itemText(0)


def test_el_detalle_no_menciona_la_mora_cuando_no_hay(prestamos):
    prestamos._on_pending_installments_loaded(
        SimpleNamespace(installments=[_cuota(2, "240000.00", "0.00")])
    )

    assert _monto(prestamos._payment_amount_display) == Decimal("240000.00")
    assert prestamos._payment_breakdown.isHidden()


def test_las_dos_pantallas_muestran_el_mismo_total(caja, prestamos):
    """Misma regla, dos entradas: si divergen, el cobro depende de por dónde
    se haya entrado, que es exactamente lo que BR-CAJA-005 no quiso decir."""
    caja._on_schedule_loaded(_CRONOGRAMA)
    prestamos._on_pending_installments_loaded(_CRONOGRAMA)

    assert (
        _monto(caja._collection_amount)
        == _monto(prestamos._payment_amount_display)
        == Decimal("241064.00")
    )


# ---- Doble clic --------------------------------------------------------------


def test_el_boton_de_cobrar_se_apaga_mientras_viaja_el_cobro(caja):
    """Un segundo clic antes de que vuelva el RPC manda un segundo cobro.

    Contra la misma cuota el servidor lo rechaza (bloquea la fila del
    préstamo y ve que ya está saldada), así que no se cobra dos veces; pero
    si la cuota elegida fuese otra, ese segundo cobro es válido y se registra
    sin que nadie lo haya pedido. Apagar el botón es la única barrera del
    lado del operador.
    """
    caja._on_schedule_loaded(_CRONOGRAMA)
    caja._run_collection = lambda *a, on_success, on_failure=None, **k: None
    assert caja._collect_button.isEnabled()

    caja._on_collect()

    assert not caja._collect_button.isEnabled()


def test_si_el_cobro_falla_el_boton_vuelve(caja):
    """Si no volviera, un error de red dejaría la caja sin poder cobrar hasta
    recargar la pantalla."""
    caja._on_schedule_loaded(_CRONOGRAMA)
    fallar = {}

    def _capturar(*a, on_success, on_failure=None, **k):
        fallar["cb"] = on_failure

    caja._run_collection = _capturar
    caja._on_collect()
    assert not caja._collect_button.isEnabled()

    fallar["cb"]("no se pudo conectar")

    assert caja._collect_button.isEnabled()


def test_el_detalle_del_prestamo_apaga_su_boton_igual(prestamos):
    prestamos._on_pending_installments_loaded(_CRONOGRAMA)
    prestamos._record_payment_button.setEnabled(True)
    # Acá el medio por defecto es Transferencia -- al revés que en la caja,
    # que es la ventanilla y arranca en Efectivo. Sin referencia, el handler
    # se iría por la validación antes de llegar al cobro.
    prestamos._payment_reference_input.setText("TRF-1")
    prestamos._run_loan_action = lambda *a, on_success, on_failure=None, **k: None

    prestamos._on_record_payment()

    assert not prestamos._record_payment_button.isEnabled()
