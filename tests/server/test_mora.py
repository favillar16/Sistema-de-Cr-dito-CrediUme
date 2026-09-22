"""BR-LOAN-017: interés moratorio por cuota vencida.

Archivo propio, con el mismo criterio que test_loan_deletion.py: lo que hay
que probar acá no es quién puede llamar qué (eso ya lo cubre
test_loan_interceptor_integration.py, y la mora no agregó ninguna RPC), sino
la aritmética del devengo y el hecho de que el cobro no la deje pasar.

Tres cosas que estas pruebas fijan y que son decisiones, no detalles:

1.  **La gracia se cuenta, no se descuenta.** La cláusula firmada dice que la
    mora "se devengará a partir de los 5 días corridos contados desde la fecha
    de su primer vencimiento": al sexto día de atraso se debe **un** día de
    mora, no seis.
2.  **No es retroactiva.** `LOAN_LATE_FEE_START_DATE` es un piso de calendario
    que existe porque la mora se deriva de fechas y no se persiste: sin él, el
    día que la regla entró en vigencia toda la cartera vencida habría aparecido
    debiendo meses de recargo que nunca se cobraron.
3.  **Se cobra con la cuota y no se puede cobrar de menos**, y lo cobrado se
    guarda aparte de `amount`, que sigue siendo lo imputado al cronograma.

Las fechas se escriben directo en la fila: `CreateLoan` rechaza un primer
vencimiento anterior a hoy (y hace bien), así que una cuota vencida no se
puede construir por la RPC. Es el mismo recurso que `_create_loan` usa para la
tasa.
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import loan_service_pb2
import pytest

from cas_server import config
from cas_server.db.base import SessionLocal
from cas_server.db.models import Client, Loan, LoanPayment
from cas_server.services.amortization import calcular_mora_cuota
from cas_server.services.loan_service import LoanServicer

from tests.server.helpers import FakeContext

# Cuota redonda: capital 1.200.000 a 12 meses y tasa 0 deja cuotas de
# 100.000, así que los montos de mora se pueden verificar a mano.
_CAPITAL = Decimal("1200000.00")
_CUOTA = Decimal("100000.00")


@pytest.fixture
def servicer():
    return LoanServicer()


def _crear_cliente() -> uuid.UUID:
    with SessionLocal() as session:
        cliente = Client(
            first_name="Mora",
            last_name="Deudora",
            national_id=f"MORA{uuid.uuid4().hex[:8]}",
            email=f"{uuid.uuid4().hex[:8]}@example.com",
            phone_number="0981000000",
            address="Coronel Oviedo",
            date_of_birth=date(1990, 1, 1),
            declared_monthly_income=Decimal("10000000.00"),
        )
        session.add(cliente)
        session.commit()
        return cliente.id


def _prestamo_activo_vencido(servicer, *, dias_de_atraso: int) -> str:
    """Préstamo ACTIVE cuya primera cuota venció hace `dias_de_atraso` días.

    La tasa se fuerza a 0 y el primer vencimiento se escribe a mano por la
    razón del docstring del módulo.
    """
    client_id = _crear_cliente()
    creado = servicer.CreateLoan(
        loan_service_pb2.CreateLoanRequest(
            client_id=str(client_id),
            principal_amount=str(_CAPITAL),
            interest_rate="",
            term_months=12,
        ),
        FakeContext(),
    )
    servicer.ApproveLoan(
        loan_service_pb2.ApproveLoanRequest(loan_id=creado.loan_id), FakeContext()
    )
    servicer.DisburseLoan(
        loan_service_pb2.DisburseLoanRequest(loan_id=creado.loan_id), FakeContext()
    )
    with SessionLocal() as session:
        prestamo = session.get(Loan, uuid.UUID(creado.loan_id))
        prestamo.interest_rate = Decimal("0.0000")
        prestamo.first_due_date = date.today() - timedelta(days=dias_de_atraso)
        session.commit()
    return creado.loan_id


@pytest.fixture
def mora_ya_vigente(monkeypatch):
    """Corre la fecha de activación bien atrás para que el piso no interfiera.

    El default apunta al futuro mientras la regla no se despliega, así que sin
    esto todas las pruebas de devengo medirían cero y pasarían por la razón
    equivocada.
    """
    monkeypatch.setattr(config, "LOAN_LATE_FEE_START_DATE", date(2000, 1, 1))


# ---- Devengo (función pura) ------------------------------------------------


@pytest.mark.parametrize(
    "dias_de_atraso, dias_punibles",
    [
        (0, 0),
        (4, 0),  # dentro de la gracia
        (5, 0),  # el quinto día todavía no devenga
        (6, 1),  # recién el sexto
        (35, 30),
        (65, 60),
    ],
)
def test_la_gracia_se_cuenta_desde_el_vencimiento(dias_de_atraso, dias_punibles):
    vencimiento = date(2026, 11, 1)
    mora = calcular_mora_cuota(
        1,
        _CUOTA,
        vencimiento,
        vencimiento + timedelta(days=dias_de_atraso),
        tasa_mensual=config.LOAN_LATE_FEE_MONTHLY_RATE,
        dias_gracia=config.LOAN_LATE_FEE_GRACE_DAYS,
        dias_por_mes=config.LOAN_LATE_FEE_DAYS_PER_MONTH,
    )

    assert mora.dias_punibles == dias_punibles
    esperado = (
        _CUOTA
        * config.LOAN_LATE_FEE_MONTHLY_RATE
        / Decimal(config.LOAN_LATE_FEE_DAYS_PER_MONTH)
        * dias_punibles
    ).quantize(Decimal("0.01"))
    assert mora.monto == esperado


def test_un_mes_entero_de_atraso_devenga_la_tasa_mensual_completa():
    """La cifra que la entidad reconoce: 0,38% de la cuota por mes de atraso."""
    vencimiento = date(2026, 11, 1)
    mora = calcular_mora_cuota(
        1,
        _CUOTA,
        vencimiento,
        # 30 días punibles = un mes, contados desde el fin de la gracia.
        vencimiento + timedelta(days=config.LOAN_LATE_FEE_GRACE_DAYS + 30),
        tasa_mensual=config.LOAN_LATE_FEE_MONTHLY_RATE,
        dias_gracia=config.LOAN_LATE_FEE_GRACE_DAYS,
        dias_por_mes=config.LOAN_LATE_FEE_DAYS_PER_MONTH,
    )

    assert mora.monto == (_CUOTA * config.LOAN_LATE_FEE_MONTHLY_RATE).quantize(
        Decimal("0.01")
    )


def test_la_mora_no_corre_antes_de_la_fecha_de_activacion():
    """Una cuota vencida hace meses no arrastra mora de antes de la regla."""
    vencimiento = date(2026, 1, 1)
    activacion = date(2026, 10, 1)

    mora = calcular_mora_cuota(
        1,
        _CUOTA,
        vencimiento,
        activacion + timedelta(days=10),
        tasa_mensual=config.LOAN_LATE_FEE_MONTHLY_RATE,
        dias_gracia=config.LOAN_LATE_FEE_GRACE_DAYS,
        dias_por_mes=config.LOAN_LATE_FEE_DAYS_PER_MONTH,
        desde=activacion,
    )

    # 10 días desde la activación, no los ~9 meses desde el vencimiento.
    assert mora.dias_punibles == 10


def test_una_cuota_saldada_no_devenga():
    mora = calcular_mora_cuota(
        1,
        Decimal("0.00"),
        date(2026, 1, 1),
        date(2026, 12, 1),
        tasa_mensual=config.LOAN_LATE_FEE_MONTHLY_RATE,
        dias_gracia=config.LOAN_LATE_FEE_GRACE_DAYS,
        dias_por_mes=config.LOAN_LATE_FEE_DAYS_PER_MONTH,
    )
    assert mora.monto == Decimal("0.00")


# ---- Devengo sobre un préstamo real ---------------------------------------


def test_el_prestamo_informa_la_mora_devengada(servicer, mora_ya_vigente):
    loan_id = _prestamo_activo_vencido(servicer, dias_de_atraso=35)

    detalle = servicer.GetLoanById(
        loan_service_pb2.GetLoanByIdRequest(loan_id=loan_id), FakeContext()
    )

    # A 35 días del primer vencimiento hay DOS cuotas vencidas (la segunda
    # venció hace 4 o 5 días), pero sólo la primera pasó los 5 días de gracia:
    # la gracia corre por cuota, no por préstamo.
    assert detalle.overdue_installments_count == 2
    esperado = (
        _CUOTA
        * config.LOAN_LATE_FEE_MONTHLY_RATE
        / Decimal(config.LOAN_LATE_FEE_DAYS_PER_MONTH)
        * 30
    ).quantize(Decimal("0.01"))
    assert Decimal(detalle.accrued_late_fee) == esperado
    # BR-LOAN-009 sigue informando las cuotas vencidas en sí, sin la mora: son
    # dos cifras distintas y sumarlas sería cobrar dos veces.
    assert Decimal(detalle.overdue_amount) == _CUOTA * 2


def test_sin_atraso_no_hay_mora(servicer, mora_ya_vigente):
    loan_id = _prestamo_activo_vencido(servicer, dias_de_atraso=0)

    detalle = servicer.GetLoanById(
        loan_service_pb2.GetLoanByIdRequest(loan_id=loan_id), FakeContext()
    )
    assert Decimal(detalle.accrued_late_fee) == Decimal("0.00")


def test_antes_de_la_activacion_la_cartera_vencida_no_muestra_mora(servicer):
    """Sin monkeypatch: con el piso por delante, un préstamo atrasado desde
    hace meses sigue informando cero. Es lo que evita que el despliegue le
    invente una deuda a toda la cartera."""
    monto_futuro = date.today() + timedelta(days=30)
    loan_id = _prestamo_activo_vencido(servicer, dias_de_atraso=120)

    with SessionLocal() as session:
        # La activación por delante de hoy, como en el despliegue real.
        config_anterior = config.LOAN_LATE_FEE_START_DATE
        config.LOAN_LATE_FEE_START_DATE = monto_futuro
        try:
            detalle = servicer.GetLoanById(
                loan_service_pb2.GetLoanByIdRequest(loan_id=loan_id), FakeContext()
            )
        finally:
            config.LOAN_LATE_FEE_START_DATE = config_anterior
        session.expire_all()

    assert Decimal(detalle.accrued_late_fee) == Decimal("0.00")


def test_el_cronograma_muestra_la_mora_de_cada_cuota(servicer, mora_ya_vigente):
    loan_id = _prestamo_activo_vencido(servicer, dias_de_atraso=35)

    cronograma = servicer.GetAmortizationSchedule(
        loan_service_pb2.GetAmortizationScheduleRequest(loan_id=loan_id), FakeContext()
    )

    primera, segunda = cronograma.installments[0], cronograma.installments[1]
    assert primera.late_fee_days == 30
    assert Decimal(primera.late_fee) > Decimal("0.00")
    # La segunda todavía no venció.
    assert segunda.late_fee_days == 0
    assert Decimal(segunda.late_fee) == Decimal("0.00")


# ---- Cobro -----------------------------------------------------------------


def test_cobrar_una_cuota_vencida_cobra_tambien_su_mora(servicer, mora_ya_vigente):
    loan_id = _prestamo_activo_vencido(servicer, dias_de_atraso=35)

    respuesta = servicer.RecordPayment(
        loan_service_pb2.RecordPaymentRequest(
            loan_id=loan_id, transfer_reference="TRF-MORA", installment_number=1
        ),
        FakeContext(),
    )

    mora = Decimal(respuesta.late_fee_amount)
    assert mora > Decimal("0.00")
    # amount_paid es lo que va al cronograma; total_charged lo que entregó el
    # cliente. Confundirlos es lo que dejaría el arqueo corto.
    assert Decimal(respuesta.amount_paid) == _CUOTA
    assert Decimal(respuesta.total_charged) == _CUOTA + mora


def test_la_mora_cobrada_no_adelanta_la_cancelacion_del_prestamo(
    servicer, mora_ya_vigente
):
    """`amount` sigue siendo sólo lo imputado al cronograma.

    Si la mora se sumara ahí, el préstamo llegaría a PAID antes de haber
    amortizado todo el capital -- el deudor quedaría debiendo una cuota que el
    sistema da por saldada.
    """
    loan_id = _prestamo_activo_vencido(servicer, dias_de_atraso=35)

    servicer.RecordPayment(
        loan_service_pb2.RecordPaymentRequest(
            loan_id=loan_id, transfer_reference="TRF-MORA", installment_number=1
        ),
        FakeContext(),
    )

    with SessionLocal() as session:
        pago = session.query(LoanPayment).one()
        assert pago.amount == _CUOTA
        assert pago.late_fee_amount > Decimal("0.00")

    detalle = servicer.GetLoanById(
        loan_service_pb2.GetLoanByIdRequest(loan_id=loan_id), FakeContext()
    )
    assert detalle.status == "ACTIVE"
    assert Decimal(detalle.total_paid) == _CUOTA
    assert Decimal(detalle.remaining_balance) == _CAPITAL - _CUOTA


def test_cobrar_la_cuota_apaga_su_mora(servicer, mora_ya_vigente):
    """Una vez cubierta, esa cuota deja de devengar: la mora no sigue
    corriendo sobre algo que ya se pagó."""
    loan_id = _prestamo_activo_vencido(servicer, dias_de_atraso=35)

    servicer.RecordPayment(
        loan_service_pb2.RecordPaymentRequest(
            loan_id=loan_id, transfer_reference="TRF-MORA", installment_number=1
        ),
        FakeContext(),
    )

    detalle = servicer.GetLoanById(
        loan_service_pb2.GetLoanByIdRequest(loan_id=loan_id), FakeContext()
    )
    assert Decimal(detalle.accrued_late_fee) == Decimal("0.00")


def test_el_historial_devuelve_la_mora_guardada_no_una_recalculada(
    servicer, mora_ya_vigente
):
    """BR-LOAN-016 + BR-LOAN-017: el comprobante reimpreso tiene que decir lo
    mismo que el original.

    Recalcular la mora al reimprimir daría cero -- esa cuota ya está cubierta
    y no devenga más -- y el papel diría que no se cobró mora cuando sí se
    cobró. Por eso es la única cifra del historial que se lee de la fila.
    """
    loan_id = _prestamo_activo_vencido(servicer, dias_de_atraso=35)
    cobro = servicer.RecordPayment(
        loan_service_pb2.RecordPaymentRequest(
            loan_id=loan_id, transfer_reference="TRF-MORA", installment_number=1
        ),
        FakeContext(),
    )

    historial = servicer.ListLoanPayments(
        loan_service_pb2.ListLoanPaymentsRequest(loan_id=loan_id), FakeContext()
    )

    entrada = historial.payments[0]
    assert Decimal(entrada.late_fee_amount) == Decimal(cobro.late_fee_amount)
    assert Decimal(entrada.total_charged) == Decimal(cobro.total_charged)


def test_un_cobro_anterior_a_la_regla_informa_mora_vacia(servicer):
    """Sin backfill: los pagos viejos no tienen la columna y el papel lo dice,
    en vez de atribuirles una mora que nadie cobró."""
    loan_id = _prestamo_activo_vencido(servicer, dias_de_atraso=0)
    servicer.RecordPayment(
        loan_service_pb2.RecordPaymentRequest(
            loan_id=loan_id, transfer_reference="TRF-VIEJA", installment_number=1
        ),
        FakeContext(),
    )
    with SessionLocal() as session:
        pago = session.query(LoanPayment).one()
        pago.late_fee_amount = None  # como quedaron las filas anteriores
        session.commit()

    historial = servicer.ListLoanPayments(
        loan_service_pb2.ListLoanPaymentsRequest(loan_id=loan_id), FakeContext()
    )

    assert historial.payments[0].late_fee_amount == ""


def test_un_prestamo_no_activo_no_devenga_mora(servicer, mora_ya_vigente):
    """Mismo alcance que BR-LOAN-009: sin cuotas corriendo no hay atraso que
    cobrar. Como la mora se deriva de fechas, un préstamo que vuelve de
    DEFAULTED (BR-LOAN-014) recupera la suya sin reconstruir nada."""
    loan_id = _prestamo_activo_vencido(servicer, dias_de_atraso=35)
    servicer.MarkDefaulted(
        loan_service_pb2.MarkDefaultedRequest(loan_id=loan_id), FakeContext()
    )

    detalle = servicer.GetLoanById(
        loan_service_pb2.GetLoanByIdRequest(loan_id=loan_id), FakeContext()
    )
    assert Decimal(detalle.accrued_late_fee) == Decimal("0.00")

    servicer.RevertDefault(
        loan_service_pb2.RevertDefaultRequest(
            loan_id=loan_id, reason="regularizó el atraso"
        ),
        FakeContext(),
    )
    detalle = servicer.GetLoanById(
        loan_service_pb2.GetLoanByIdRequest(loan_id=loan_id), FakeContext()
    )
    assert Decimal(detalle.accrued_late_fee) > Decimal("0.00")


def test_la_fecha_de_activacion_no_quedo_en_el_pasado():
    """Guardián de despliegue, no de lógica.

    La fecha de activación es lo único que separa "la mora empieza a correr"
    de "toda la cartera vencida aparece debiendo meses". Si alguien la mueve
    hacia atrás sin querer, esto lo dice antes de que llegue a producción.
    Moverla a propósito es una decisión comercial: hay que actualizar también
    este test y el spec.

    Mira el **default** y no `LOAN_LATE_FEE_START_DATE`, que es el valor
    efectivo: el override por entorno existe justamente para poder probar la
    mora antes de esa fecha (en un equipo de desarrollo, con el default, todo
    da cero), y un `.env` local no tiene por qué romper la suite. Lo que se
    despliega es el default.
    """
    assert config.LOAN_LATE_FEE_DEFAULT_START_DATE >= date(2026, 10, 1)


def test_la_caja_recibe_la_cuota_mas_la_mora(servicer, mora_ya_vigente):
    """BR-CAJA-003/BR-LOAN-017: el movimiento de caja imputa lo que entró por
    ventanilla, no sólo la parte que fue al cronograma.

    Se verifica sobre el helper de cash_service directamente porque el cobro
    en efectivo exige credenciales y caja abierta, que es territorio de
    test_cash_interceptor_integration.py; acá lo que importa es que la cifra
    que se imputa sea la suma.
    """
    from cas_server.services.cash_service import registrar_cobro_en_efectivo

    pago = LoanPayment(
        loan_id=uuid.uuid4(),
        amount=Decimal("100000.00"),
        late_fee_amount=Decimal("1250.00"),
        paid_at=datetime.now(timezone.utc),
    )
    sesion_caja = type("SesionFalsa", (), {"id": uuid.uuid4()})()
    capturado = []
    sesion_falsa = type("Sesion", (), {"add": lambda self, x: capturado.append(x)})()

    movimiento = registrar_cobro_en_efectivo(
        sesion_falsa, sesion_caja, pago, None, datetime.now(timezone.utc)
    )

    assert movimiento.amount == Decimal("101250.00")
