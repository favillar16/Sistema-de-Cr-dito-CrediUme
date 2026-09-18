"""Reglas de invalidación del arqueo (BR-CAJA-003) en CashView.

Mismo motivo que test_view_session_state.py para vivir como test de widget
real en vez de función pura: es estado de la vista entre un cierre de caja y
el papel que se imprime después, no algo que documents.py pueda cubrir solo.

    1. El arqueo pertenece a UN cierre. Recién aparece después de cerrar la
       caja, y vuelve a desaparecer al abrir un turno nuevo -- seguir
       ofreciéndolo se leería como si describiera el turno en curso.
    2. El arqueo pertenece a UNA sesión de operador, igual que el
       comprobante de pago: un cambio de usuario tiene que borrarlo.
"""

import os
from datetime import datetime
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from cas_client.session import Session  # noqa: E402
from cas_client.views.cash_view import CashView  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class _StubClient:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: None


@pytest.fixture
def view(app):
    session = Session()
    session.access_token = "token"
    session.username = "cajero1"
    vista = CashView(
        client=_StubClient(),
        clients_client=_StubClient(),
        loans_client=_StubClient(),
        dashboard_client=_StubClient(),
        session=session,
    )
    vista.refresh = lambda: None
    return vista


def _detalle_cerrado(**kwargs):
    base = dict(
        id="ses-1",
        cashier_username="cajero1",
        cashier_full_name="Cajero Uno",
        status="CLOSED",
        opening_amount="500000.00",
        opening_notes="",
        opened_at=SimpleNamespace(ToDatetime=lambda: datetime(2026, 9, 18, 8)),
        total_income="0.00",
        total_expense="0.00",
        total_loan_collections="0.00",
        expected_amount="500000.00",
        movements=[],
        movements_count=0,
        closing_counted_amount="500000.00",
        closing_expected_amount="500000.00",
        closing_difference="0.00",
        closed_at=SimpleNamespace(ToDatetime=lambda: datetime(2026, 9, 18, 18)),
        closing_notes="",
        closed_by_username="cajero1",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_no_hay_arqueo_para_ofrecer_antes_del_primer_cierre(view):
    # isHidden() y no isVisible(): la vista nunca se muestra en este test
    # (no hay show() de la ventana), así que isVisible() daría False para
    # cualquier widget sea cual sea el setVisible() que se le haya aplicado.
    # isHidden() sí refleja el último setVisible()/hide() propio del widget.
    assert view._last_closed_session is None
    assert view._arqueo_card.isHidden()


def test_cerrar_la_caja_habilita_el_arqueo(view):
    view._on_closed(_detalle_cerrado())

    assert view._last_closed_session is not None
    assert not view._arqueo_section.isHidden()
    assert not view._arqueo_card.isHidden()


def test_abrir_un_turno_nuevo_apaga_el_arqueo_del_anterior(view):
    """El caso que motiva el pin: sin esto, un cajero que cierra, ve el
    arqueo, abre un turno nuevo y sigue viendo el botón habilitado imprimiría
    -- sin querer -- el cierre de ayer sobre la pantalla de hoy."""
    view._on_closed(_detalle_cerrado())

    view._on_opened(SimpleNamespace())

    assert view._last_closed_session is None
    assert view._arqueo_card.isHidden()


def test_cambiar_de_operador_borra_el_arqueo(view):
    """Misma regla que el comprobante de pago: la PC de caja es compartida,
    el próximo cajero no puede encontrarse el arqueo del anterior en
    pantalla."""
    view._on_closed(_detalle_cerrado())

    view.set_user("otro_cajero", "CASHIER")

    assert view._last_closed_session is None
    assert view._arqueo_card.isHidden()


def test_sin_cierre_los_papeles_no_se_emiten(view):
    """Guarda de fondo, mismo criterio que el comprobante: los tres
    handlers salen temprano si no hay un cierre en memoria."""
    view._on_arqueo_print()
    view._on_arqueo_pdf()
    view._on_arqueo_docx()
