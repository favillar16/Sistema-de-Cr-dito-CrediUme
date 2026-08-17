"""Cobertura del módulo de caja (BR-CAJA-001..005).

A diferencia del resto de los servicios, la caja NO tiene un
`test_cash_service.py` que llame al servicer directamente: cada RPC resuelve
el turno sobre el que opera a partir del usuario del token, así que un
llamado sin credenciales no es un caso degradado a tolerar sino un error
(`_credenciales_o_abortar`). Por eso todo se ejercita acá, contra un
`grpc.Server` real con AuthInterceptor -- el mismo patrón de
test_interceptor_integration.py -- donde las credenciales salen de un login
de verdad.

El servidor de este módulo registra también LoanServicer, porque BR-CAJA-004
(cobro de cuota en efectivo imputado a la caja) cruza los dos servicios y no
se puede probar desde uno solo.
"""

import uuid
from concurrent import futures
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import auth_service_pb2
import auth_service_pb2_grpc
import cash_service_pb2
import cash_service_pb2_grpc
import grpc
import loan_service_pb2
import loan_service_pb2_grpc
import pytest

from cas_server.db.base import SessionLocal
from cas_server.db.models import (
    CashMovement,
    CashSession,
    Client,
    Loan,
    LoanPayment,
    LoanStatusEnum,
    PaymentMethodEnum,
    RoleEnum,
    User,
)
from cas_server.security.interceptor import AuthInterceptor
from cas_server.security.passwords import hash_password
from cas_server.services.auth_service import AuthServicer
from cas_server.services.cash_service import CashServicer
from cas_server.services.loan_service import LoanServicer


@pytest.fixture
def stubs():
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=4), interceptors=[AuthInterceptor()]
    )
    auth_service_pb2_grpc.add_AuthServiceServicer_to_server(AuthServicer(), server)
    cash_service_pb2_grpc.add_CashServiceServicer_to_server(CashServicer(), server)
    loan_service_pb2_grpc.add_LoanServiceServicer_to_server(LoanServicer(), server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()

    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    try:
        yield (
            auth_service_pb2_grpc.AuthServiceStub(channel),
            cash_service_pb2_grpc.CashServiceStub(channel),
            loan_service_pb2_grpc.LoanServiceStub(channel),
        )
    finally:
        channel.close()
        server.stop(grace=None)


def _create_user(username, password, role, **datos_personales):
    with SessionLocal() as session:
        session.add(
            User(
                username=username,
                password_hash=hash_password(password),
                role=role,
                **datos_personales,
            )
        )
        session.commit()


def _login(auth_stub, username, password="Passw0rd!"):
    response = auth_stub.Login(
        auth_service_pb2.LoginRequest(username=username, password=password)
    )
    return (("authorization", f"Bearer {response.access_token}"),)


def _cajero(auth_stub, username="cajero", role=RoleEnum.CASHIER, **datos_personales):
    _create_user(username, "Passw0rd!", role, **datos_personales)
    return _login(auth_stub, username)


def _abrir(cash_stub, metadata, monto="500000.00", notes=""):
    return cash_stub.OpenCashSession(
        cash_service_pb2.OpenCashSessionRequest(opening_amount=monto, notes=notes),
        metadata=metadata,
    )


def _movimiento(cash_stub, metadata, tipo, monto, concepto="Concepto de prueba"):
    return cash_stub.RegisterCashMovement(
        cash_service_pb2.RegisterCashMovementRequest(
            movement_type=tipo, amount=monto, concept=concepto
        ),
        metadata=metadata,
    )


def _cerrar(cash_stub, metadata, contado, notes="", session_id=""):
    return cash_stub.CloseCashSession(
        cash_service_pb2.CloseCashSessionRequest(
            counted_amount=contado, notes=notes, session_id=session_id
        ),
        metadata=metadata,
    )


def _crear_prestamo_activo(national_id="9000001", email="caja@example.com"):
    """Préstamo ACTIVE listo para cobrarle una cuota. La tasa/plazo son los
    mismos que usa test_loan_interceptor_integration.py -- acá solo importa
    que exista una cuota que cobrar."""
    with SessionLocal() as session:
        cliente = Client(
            first_name="Caja",
            last_name="Cliente",
            national_id=national_id,
            email=email,
            phone_number="0981555555",
            date_of_birth=date(1990, 1, 1),
            address="Calle Caja 1",
            declared_monthly_income=Decimal("5000.00"),
            is_active=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(cliente)
        session.flush()
        prestamo = Loan(
            client_id=cliente.id,
            principal_amount=Decimal("1000.00"),
            interest_rate=Decimal("0.10"),
            term_months=6,
            first_due_date=datetime.now(timezone.utc).date(),
            status=LoanStatusEnum.ACTIVE,
            created_at=datetime.now(timezone.utc),
            approved_at=datetime.now(timezone.utc),
        )
        session.add(prestamo)
        session.commit()
        session.refresh(prestamo)
        return prestamo.id


# ---- BR-CAJA-001: apertura de turno ---------------------------------------


def test_open_cash_session_returns_the_opening_amount_as_expected(stubs):
    auth_stub, cash_stub, _ = stubs
    metadata = _cajero(auth_stub)

    detalle = _abrir(cash_stub, metadata, "500000.00", notes="Turno mañana")

    assert detalle.status == "OPEN"
    assert detalle.opening_amount == "500000.00"
    # Sin movimientos todavía, lo que debería haber en el cajón es
    # exactamente lo que se declaró al abrir.
    assert detalle.expected_amount == "500000.00"
    assert detalle.total_income == "0.00"
    assert detalle.total_expense == "0.00"
    assert detalle.opening_notes == "Turno mañana"
    assert detalle.cashier_username == "cajero"


def test_open_cash_session_accepts_a_zero_opening_amount(stubs):
    """Abrir sin fondo fijo es normal -- solo un monto negativo es un error."""
    auth_stub, cash_stub, _ = stubs
    metadata = _cajero(auth_stub)

    detalle = _abrir(cash_stub, metadata, "0")

    assert detalle.expected_amount == "0.00"


def test_open_cash_session_rejects_a_negative_opening_amount(stubs):
    auth_stub, cash_stub, _ = stubs
    metadata = _cajero(auth_stub)

    with pytest.raises(grpc.RpcError) as exc_info:
        _abrir(cash_stub, metadata, "-1.00")
    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT


def test_second_open_session_for_the_same_user_is_rejected(stubs):
    """BR-CAJA-002. Lo impone el índice único parcial sobre las filas OPEN,
    no una verificación previa -- ver el comentario en models.py."""
    auth_stub, cash_stub, _ = stubs
    metadata = _cajero(auth_stub)
    _abrir(cash_stub, metadata)

    with pytest.raises(grpc.RpcError) as exc_info:
        _abrir(cash_stub, metadata)
    assert exc_info.value.code() == grpc.StatusCode.ALREADY_EXISTS


def test_two_users_can_each_have_their_own_open_session(stubs):
    """El límite de BR-CAJA-002 es por cajero, no global."""
    auth_stub, cash_stub, _ = stubs
    metadata_uno = _cajero(auth_stub, "cajero_uno")
    metadata_dos = _cajero(auth_stub, "cajero_dos")

    _abrir(cash_stub, metadata_uno, "100.00")
    detalle = _abrir(cash_stub, metadata_dos, "200.00")

    assert detalle.expected_amount == "200.00"


def test_reopening_after_closing_is_allowed(stubs):
    """El índice único solo alcanza a las filas OPEN, así que cerrar libera
    al cajero para abrir el turno siguiente."""
    auth_stub, cash_stub, _ = stubs
    metadata = _cajero(auth_stub)
    _abrir(cash_stub, metadata, "100.00")
    _cerrar(cash_stub, metadata, "100.00")

    detalle = _abrir(cash_stub, metadata, "150.00")

    assert detalle.status == "OPEN"
    assert detalle.opening_amount == "150.00"


def test_get_current_cash_session_reports_no_open_session(stubs):
    """No tener caja abierta es un estado normal, no un NOT_FOUND -- la
    pantalla lo necesita para ofrecer "Abrir caja"."""
    auth_stub, cash_stub, _ = stubs
    metadata = _cajero(auth_stub)

    respuesta = cash_stub.GetCurrentCashSession(
        cash_service_pb2.GetCurrentCashSessionRequest(), metadata=metadata
    )

    assert respuesta.has_open_session is False


def test_get_current_cash_session_only_sees_the_callers_own(stubs):
    auth_stub, cash_stub, _ = stubs
    metadata_uno = _cajero(auth_stub, "cajero_uno")
    metadata_dos = _cajero(auth_stub, "cajero_dos")
    _abrir(cash_stub, metadata_uno, "100.00")

    respuesta = cash_stub.GetCurrentCashSession(
        cash_service_pb2.GetCurrentCashSessionRequest(), metadata=metadata_dos
    )

    assert respuesta.has_open_session is False


# ---- BR-CAJA-002: movimientos ---------------------------------------------


def test_movements_update_income_expense_and_expected(stubs):
    auth_stub, cash_stub, _ = stubs
    metadata = _cajero(auth_stub)
    _abrir(cash_stub, metadata, "500000.00")

    _movimiento(cash_stub, metadata, "INGRESO", "120000.00", "Reposición")
    detalle = _movimiento(cash_stub, metadata, "EGRESO", "20000.00", "Compra insumos")

    assert detalle.total_income == "120000.00"
    assert detalle.total_expense == "20000.00"
    assert detalle.expected_amount == "600000.00"
    assert detalle.movements_count == 2
    # Los movimientos cargados a mano no son automáticos: los distingue el
    # vínculo con un pago, que acá no existe.
    assert all(not m.is_automatic for m in detalle.movements)


def test_movement_requires_an_open_session(stubs):
    auth_stub, cash_stub, _ = stubs
    metadata = _cajero(auth_stub)

    with pytest.raises(grpc.RpcError) as exc_info:
        _movimiento(cash_stub, metadata, "INGRESO", "100.00")
    assert exc_info.value.code() == grpc.StatusCode.FAILED_PRECONDITION


def test_expense_greater_than_available_cash_is_rejected(stubs):
    """No se puede sacar efectivo que no está en el cajón."""
    auth_stub, cash_stub, _ = stubs
    metadata = _cajero(auth_stub)
    _abrir(cash_stub, metadata, "1000.00")

    with pytest.raises(grpc.RpcError) as exc_info:
        _movimiento(cash_stub, metadata, "EGRESO", "1000.01", "De más")
    assert exc_info.value.code() == grpc.StatusCode.FAILED_PRECONDITION


def test_expense_equal_to_available_cash_is_allowed(stubs):
    """El límite del test anterior es "más que", no "tanto como": vaciar la
    caja es legítimo."""
    auth_stub, cash_stub, _ = stubs
    metadata = _cajero(auth_stub)
    _abrir(cash_stub, metadata, "1000.00")

    detalle = _movimiento(cash_stub, metadata, "EGRESO", "1000.00", "Vaciado")

    assert detalle.expected_amount == "0.00"


def test_movement_rejects_an_unknown_type(stubs):
    auth_stub, cash_stub, _ = stubs
    metadata = _cajero(auth_stub)
    _abrir(cash_stub, metadata)

    with pytest.raises(grpc.RpcError) as exc_info:
        _movimiento(cash_stub, metadata, "TRANSFERENCIA", "100.00")
    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT


def test_movement_requires_a_concept(stubs):
    """Un arqueo con movimientos sin concepto no es auditable."""
    auth_stub, cash_stub, _ = stubs
    metadata = _cajero(auth_stub)
    _abrir(cash_stub, metadata)

    with pytest.raises(grpc.RpcError) as exc_info:
        _movimiento(cash_stub, metadata, "INGRESO", "100.00", concepto="   ")
    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT


def test_movement_rejects_a_zero_amount(stubs):
    auth_stub, cash_stub, _ = stubs
    metadata = _cajero(auth_stub)
    _abrir(cash_stub, metadata)

    with pytest.raises(grpc.RpcError) as exc_info:
        _movimiento(cash_stub, metadata, "INGRESO", "0")
    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT


# ---- BR-CAJA-003: cierre y arqueo -----------------------------------------


def test_close_computes_expected_and_difference_from_the_server_side(stubs):
    """El cliente solo declara lo contado; el esperado lo recalcula el
    servidor. Es lo único que hace que el arqueo signifique algo."""
    auth_stub, cash_stub, _ = stubs
    metadata = _cajero(auth_stub)
    _abrir(cash_stub, metadata, "100000.00")
    _movimiento(cash_stub, metadata, "INGRESO", "50000.00", "Cobro varios")
    _movimiento(cash_stub, metadata, "EGRESO", "10000.00", "Gasto")

    detalle = _cerrar(cash_stub, metadata, "140000.00", notes="Cuadró")

    assert detalle.status == "CLOSED"
    assert detalle.closing_expected_amount == "140000.00"
    assert detalle.closing_counted_amount == "140000.00"
    assert detalle.closing_difference == "0.00"
    assert detalle.closing_notes == "Cuadró"
    assert detalle.closed_by_username == "cajero"


def test_close_reports_a_surplus_as_a_positive_difference(stubs):
    auth_stub, cash_stub, _ = stubs
    metadata = _cajero(auth_stub)
    _abrir(cash_stub, metadata, "100000.00")

    detalle = _cerrar(cash_stub, metadata, "100500.00")

    assert detalle.closing_difference == "500.00"


def test_close_reports_a_shortage_as_a_negative_difference(stubs):
    auth_stub, cash_stub, _ = stubs
    metadata = _cajero(auth_stub)
    _abrir(cash_stub, metadata, "100000.00")

    detalle = _cerrar(cash_stub, metadata, "99000.00")

    assert detalle.closing_difference == "-1000.00"


def test_close_requires_an_open_session(stubs):
    auth_stub, cash_stub, _ = stubs
    metadata = _cajero(auth_stub)

    with pytest.raises(grpc.RpcError) as exc_info:
        _cerrar(cash_stub, metadata, "100.00")
    assert exc_info.value.code() == grpc.StatusCode.FAILED_PRECONDITION


def test_closing_an_already_closed_session_is_rejected(stubs):
    auth_stub, cash_stub, _ = stubs
    metadata = _cajero(auth_stub)
    detalle = _abrir(cash_stub, metadata, "100.00")
    _cerrar(cash_stub, metadata, "100.00")

    with pytest.raises(grpc.RpcError) as exc_info:
        _cerrar(cash_stub, metadata, "100.00", session_id=detalle.id)
    assert exc_info.value.code() == grpc.StatusCode.FAILED_PRECONDITION


def test_a_cashier_cannot_close_another_cashiers_session(stubs):
    auth_stub, cash_stub, _ = stubs
    metadata_uno = _cajero(auth_stub, "cajero_uno")
    metadata_dos = _cajero(auth_stub, "cajero_dos")
    ajena = _abrir(cash_stub, metadata_uno, "100.00")

    with pytest.raises(grpc.RpcError) as exc_info:
        _cerrar(cash_stub, metadata_dos, "100.00", session_id=ajena.id)
    assert exc_info.value.code() == grpc.StatusCode.PERMISSION_DENIED


def test_a_manager_can_close_a_session_left_open_by_a_cashier(stubs):
    """BR-CAJA-003: el caso real es el cajero que se fue sin cerrar. El
    arqueo queda firmado por quien lo cerró, no por el dueño del turno."""
    auth_stub, cash_stub, _ = stubs
    metadata_cajero = _cajero(auth_stub, "cajero_uno")
    metadata_gerente = _cajero(auth_stub, "gerente", role=RoleEnum.MANAGER)
    abierta = _abrir(cash_stub, metadata_cajero, "100.00")

    detalle = _cerrar(
        cash_stub,
        metadata_gerente,
        "90.00",
        notes="Cerrada por supervisión",
        session_id=abierta.id,
    )

    assert detalle.status == "CLOSED"
    assert detalle.cashier_username == "cajero_uno"
    assert detalle.closed_by_username == "gerente"
    assert detalle.closing_difference == "-10.00"


def test_a_closed_session_keeps_the_expected_amount_it_was_measured_against(stubs):
    """El esperado del arqueo sale de la columna persistida, no de un
    recálculo: un arqueo cerrado es un registro histórico."""
    auth_stub, cash_stub, _ = stubs
    metadata = _cajero(auth_stub)
    abierta = _abrir(cash_stub, metadata, "100.00")
    cerrada = _cerrar(cash_stub, metadata, "80.00")

    with SessionLocal() as session:
        fila = session.get(CashSession, uuid.UUID(abierta.id))
        assert fila.closing_expected_amount == Decimal("100.00")
        assert fila.closing_counted_amount == Decimal("80.00")
        assert fila.closing_difference == Decimal("-20.00")
    assert cerrada.closing_expected_amount == "100.00"


# ---- Historial de arqueos --------------------------------------------------


def test_list_cash_sessions_shows_a_cashier_only_their_own(stubs):
    auth_stub, cash_stub, _ = stubs
    metadata_uno = _cajero(auth_stub, "cajero_uno")
    metadata_dos = _cajero(auth_stub, "cajero_dos")
    _abrir(cash_stub, metadata_uno, "100.00")
    _cerrar(cash_stub, metadata_uno, "100.00")
    _abrir(cash_stub, metadata_dos, "200.00")

    respuesta = cash_stub.ListCashSessions(
        cash_service_pb2.ListCashSessionsRequest(), metadata=metadata_uno
    )

    assert [s.cashier_username for s in respuesta.sessions] == ["cajero_uno"]


def test_list_cash_sessions_shows_a_manager_every_cashier(stubs):
    auth_stub, cash_stub, _ = stubs
    metadata_uno = _cajero(auth_stub, "cajero_uno")
    metadata_dos = _cajero(auth_stub, "cajero_dos")
    metadata_gerente = _cajero(auth_stub, "gerente", role=RoleEnum.MANAGER)
    _abrir(cash_stub, metadata_uno, "100.00")
    _abrir(cash_stub, metadata_dos, "200.00")

    respuesta = cash_stub.ListCashSessions(
        cash_service_pb2.ListCashSessionsRequest(), metadata=metadata_gerente
    )

    assert {s.cashier_username for s in respuesta.sessions} == {
        "cajero_uno",
        "cajero_dos",
    }


def test_list_cash_sessions_filters_by_date_range(stubs):
    auth_stub, cash_stub, _ = stubs
    metadata = _cajero(auth_stub)
    _abrir(cash_stub, metadata, "100.00")
    ayer = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()

    vacio = cash_stub.ListCashSessions(
        cash_service_pb2.ListCashSessionsRequest(start_date=ayer, end_date=ayer),
        metadata=metadata,
    )
    hoy = datetime.now(timezone.utc).date().isoformat()
    con_resultado = cash_stub.ListCashSessions(
        cash_service_pb2.ListCashSessionsRequest(start_date=hoy, end_date=hoy),
        metadata=metadata,
    )

    assert len(vacio.sessions) == 0
    assert len(con_resultado.sessions) == 1


def test_list_cash_sessions_rejects_an_inverted_range(stubs):
    auth_stub, cash_stub, _ = stubs
    metadata = _cajero(auth_stub)

    with pytest.raises(grpc.RpcError) as exc_info:
        cash_stub.ListCashSessions(
            cash_service_pb2.ListCashSessionsRequest(
                start_date="2026-08-10", end_date="2026-08-01"
            ),
            metadata=metadata,
        )
    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT


# ---- BR-CAJA-004: cobro de cuota en efectivo ------------------------------


def test_cash_payment_posts_an_income_movement_to_the_open_session(stubs):
    auth_stub, cash_stub, loan_stub = stubs
    metadata = _cajero(auth_stub)
    _abrir(cash_stub, metadata, "100000.00")
    loan_id = _crear_prestamo_activo()

    pago = loan_stub.RecordPayment(
        loan_service_pb2.RecordPaymentRequest(
            loan_id=str(loan_id), installment_number=1, payment_method="EFECTIVO"
        ),
        metadata=metadata,
    )

    respuesta = cash_stub.GetCurrentCashSession(
        cash_service_pb2.GetCurrentCashSessionRequest(), metadata=metadata
    )
    detalle = respuesta.session
    assert pago.payment_method == "EFECTIVO"
    assert detalle.total_loan_collections == pago.amount_paid
    assert detalle.total_income == pago.amount_paid
    assert detalle.expected_amount == str(
        Decimal("100000.00") + Decimal(pago.amount_paid)
    )
    # El movimiento automático se distingue del que carga el cajero por el
    # vínculo con el pago que lo originó.
    movimiento = detalle.movements[0]
    assert movimiento.is_automatic is True
    assert movimiento.loan_payment_id


def test_cash_payment_does_not_require_a_transfer_reference(stubs):
    """En efectivo no hay número de transferencia que pedir; exigir uno solo
    llevaría a inventarlo."""
    auth_stub, cash_stub, loan_stub = stubs
    metadata = _cajero(auth_stub)
    _abrir(cash_stub, metadata, "0")
    loan_id = _crear_prestamo_activo()

    pago = loan_stub.RecordPayment(
        loan_service_pb2.RecordPaymentRequest(
            loan_id=str(loan_id), installment_number=1, payment_method="EFECTIVO"
        ),
        metadata=metadata,
    )

    assert pago.success
    assert pago.transfer_reference == ""


def test_cash_payment_without_an_open_session_is_rejected_and_records_nothing(stubs):
    """La caja se busca antes de insertar el pago justamente para que un
    cobro sin caja no quede registrado a medias."""
    auth_stub, _, loan_stub = stubs
    metadata = _cajero(auth_stub)
    loan_id = _crear_prestamo_activo()

    with pytest.raises(grpc.RpcError) as exc_info:
        loan_stub.RecordPayment(
            loan_service_pb2.RecordPaymentRequest(
                loan_id=str(loan_id), installment_number=1, payment_method="EFECTIVO"
            ),
            metadata=metadata,
        )

    assert exc_info.value.code() == grpc.StatusCode.FAILED_PRECONDITION
    with SessionLocal() as session:
        assert session.query(LoanPayment).count() == 0
        assert session.query(CashMovement).count() == 0


def test_transfer_payment_leaves_the_cash_session_untouched(stubs):
    """Solo el efectivo pasa por la caja: una transferencia no toca el
    arqueo aunque el cajero tenga el turno abierto."""
    auth_stub, cash_stub, loan_stub = stubs
    metadata = _cajero(auth_stub)
    _abrir(cash_stub, metadata, "100000.00")
    loan_id = _crear_prestamo_activo()

    loan_stub.RecordPayment(
        loan_service_pb2.RecordPaymentRequest(
            loan_id=str(loan_id),
            installment_number=1,
            transfer_reference="TRF-001",
            payment_method="TRANSFERENCIA",
        ),
        metadata=metadata,
    )

    respuesta = cash_stub.GetCurrentCashSession(
        cash_service_pb2.GetCurrentCashSessionRequest(), metadata=metadata
    )
    assert respuesta.session.expected_amount == "100000.00"
    assert respuesta.session.movements_count == 0


def test_payment_without_a_method_still_defaults_to_transfer(stubs):
    """Compatibilidad: los llamadores anteriores a BR-CAJA-004 no mandan
    payment_method y deben seguir comportándose igual que antes -- incluida
    la referencia obligatoria."""
    auth_stub, _, loan_stub = stubs
    metadata = _cajero(auth_stub)
    loan_id = _crear_prestamo_activo()

    pago = loan_stub.RecordPayment(
        loan_service_pb2.RecordPaymentRequest(
            loan_id=str(loan_id), installment_number=1, transfer_reference="TRF-002"
        ),
        metadata=metadata,
    )

    assert pago.payment_method == "TRANSFERENCIA"
    with SessionLocal() as session:
        fila = session.query(LoanPayment).one()
        assert fila.payment_method == PaymentMethodEnum.TRANSFERENCIA


def test_transfer_payment_still_requires_a_reference(stubs):
    auth_stub, _, loan_stub = stubs
    metadata = _cajero(auth_stub)
    loan_id = _crear_prestamo_activo()

    with pytest.raises(grpc.RpcError) as exc_info:
        loan_stub.RecordPayment(
            loan_service_pb2.RecordPaymentRequest(
                loan_id=str(loan_id),
                installment_number=1,
                payment_method="TRANSFERENCIA",
            ),
            metadata=metadata,
        )
    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT


def test_payment_rejects_an_unknown_method(stubs):
    auth_stub, _, loan_stub = stubs
    metadata = _cajero(auth_stub)
    loan_id = _crear_prestamo_activo()

    with pytest.raises(grpc.RpcError) as exc_info:
        loan_stub.RecordPayment(
            loan_service_pb2.RecordPaymentRequest(
                loan_id=str(loan_id), installment_number=1, payment_method="CHEQUE"
            ),
            metadata=metadata,
        )
    assert exc_info.value.code() == grpc.StatusCode.INVALID_ARGUMENT


def test_cash_collections_are_counted_in_the_closing_arqueo(stubs):
    """El punto de BR-CAJA-004: el efectivo cobrado en ventanilla tiene que
    aparecer en el esperado del cierre sin que el cajero lo cargue a mano."""
    auth_stub, cash_stub, loan_stub = stubs
    metadata = _cajero(auth_stub)
    _abrir(cash_stub, metadata, "100000.00")
    loan_id = _crear_prestamo_activo()

    pago = loan_stub.RecordPayment(
        loan_service_pb2.RecordPaymentRequest(
            loan_id=str(loan_id), installment_number=1, payment_method="EFECTIVO"
        ),
        metadata=metadata,
    )
    esperado = Decimal("100000.00") + Decimal(pago.amount_paid)

    detalle = _cerrar(cash_stub, metadata, str(esperado))

    assert detalle.closing_expected_amount == str(esperado)
    assert detalle.closing_difference == "0.00"


# ---- RBAC ------------------------------------------------------------------


def test_cash_rpcs_require_a_token(stubs):
    _, cash_stub, _ = stubs

    with pytest.raises(grpc.RpcError) as exc_info:
        cash_stub.GetCurrentCashSession(cash_service_pb2.GetCurrentCashSessionRequest())
    assert exc_info.value.code() == grpc.StatusCode.UNAUTHENTICATED


@pytest.mark.parametrize(
    "role",
    [RoleEnum.CASHIER, RoleEnum.CREDIT_ANALYST, RoleEnum.MANAGER, RoleEnum.ADMIN],
)
def test_every_role_can_operate_its_own_cash_session(stubs, role):
    auth_stub, cash_stub, _ = stubs
    metadata = _cajero(auth_stub, f"usuario_{role.value.lower()}", role=role)

    detalle = _abrir(cash_stub, metadata, "10.00")

    assert detalle.status == "OPEN"
