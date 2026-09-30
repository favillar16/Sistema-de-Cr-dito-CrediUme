import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

import grpc

import loan_service_pb2
import loan_service_pb2_grpc
from cas_server import config
from cas_server.db.base import SessionLocal
from cas_server.db.models import (
    AuditLog,
    Client,
    Loan,
    LoanInstallmentAdjustment,
    LoanPayment,
    LoanStatusEnum,
    PaymentMethodEnum,
    User,
)
from cas_server.security.current_user import get_current_claims
from cas_server.services.amortization import (
    MoraCuota,
    calcular_cronograma,
    calcular_mora_cuota,
)

# BR-CAJA-004. La dependencia va en un solo sentido (loan_service ->
# cash_service): cash_service.py no importa nada de acá, así que no hay ciclo.
# La forma del movimiento de caja vive allá, junto al resto del arqueo, para
# que este servicer no tenga que saber cómo se compone un CashMovement.
from cas_server.services.cash_service import (
    obtener_sesion_abierta,
    registrar_cobro_en_efectivo,
)
from cas_server.services.common import (
    analizar_decimal,
    analizar_fecha,
    analizar_uuid,
    id_actor_actual,
    ip_remota,
    a_marca_tiempo,
)

CERO = Decimal("0.00")

_ESTADOS_ACTIVOS = (LoanStatusEnum.APPROVED, LoanStatusEnum.ACTIVE)

# BR-LOAN-012: estados desde los que un préstamo cargado por error se puede
# eliminar. El criterio no es "todavía no terminó" sino "todavía no movió
# dinero": ACTIVE/PAID/DEFAULTED implican un desembolso ya hecho, y borrarlos
# haría desaparecer pagos que pueden estar imputados a un arqueo de caja ya
# firmado (CashMovement.loan_payment_id -> loan_payments -> loans). Para esos
# casos existen MarkDefaulted y el resto del ciclo de vida, no el borrado.
_ESTADOS_ELIMINABLES = (
    LoanStatusEnum.PENDING,
    LoanStatusEnum.APPROVED,
    LoanStatusEnum.EXPIRED,
)


def _tal_vez_vencer_prestamo(prestamo: Loan, ahora: datetime) -> bool:
    """BR-LOAN-003: vence perezosamente un préstamo APPROVED atrasado sin desembolsar.

    Replica el patrón de vencimiento perezoso del bloqueo de cuenta en
    AuthServicer.Login. Devuelve True si el estado cambió (quien llama es
    responsable de auditar y hacer commit).
    """
    if (
        prestamo.status == LoanStatusEnum.APPROVED
        and prestamo.approved_at is not None
        and ahora - prestamo.approved_at
        >= timedelta(days=config.LOAN_APPROVAL_EXPIRY_DAYS)
    ):
        prestamo.status = LoanStatusEnum.EXPIRED
        return True
    return False


def _vencer_atrasados_y_contar_activos(
    prestamos: list[Loan], ahora: datetime
) -> tuple[int, list[Loan]]:
    """Aplica _tal_vez_vencer_prestamo a cada préstamo, devuelve (cantidad_activos, vencidos)."""
    cantidad_activos = 0
    vencidos: list[Loan] = []
    for prestamo in prestamos:
        if _tal_vez_vencer_prestamo(prestamo, ahora):
            vencidos.append(prestamo)
        if prestamo.status in _ESTADOS_ACTIVOS:
            cantidad_activos += 1
    return cantidad_activos, vencidos


def _auditar_vencidos(sesion, vencidos: list[Loan], context, ahora: datetime) -> None:
    for prestamo in vencidos:
        sesion.add(
            AuditLog(
                user_id=None,  # desencadenado por el sistema, sin actor humano
                action=f"PRESTAMO_VENCIDO loan_id={prestamo.id}",
                ip_address=ip_remota(context),
                timestamp=ahora,
            )
        )


def _ajustes_prestamo(prestamo: Loan) -> dict[int, Decimal]:
    """Excepciones manuales al cronograma (UpdateInstallmentAmount), como dict
    número de cuota -> monto fijado."""
    return {
        ajuste.installment_number: ajuste.adjusted_amount
        for ajuste in prestamo.installment_adjustments
    }


def _totales_prestamo(prestamo: Loan) -> tuple[Decimal, Decimal]:
    cronograma = calcular_cronograma(
        _monto_financiado(prestamo),
        prestamo.interest_rate,
        prestamo.term_months,
        ajustes=_ajustes_prestamo(prestamo),
    )
    total_programado = sum((fila.monto_cuota for fila in cronograma), CERO)
    total_pagado = sum((pago.amount for pago in prestamo.payments), CERO)
    return total_programado, total_pagado


def _cronograma_con_pendientes(prestamo: Loan) -> list[tuple]:
    """Camina el cronograma completo (no solo las cuotas vencidas) aplicando
    lo realmente pagado en orden -- LoanPayment no está atado a una cuota
    puntual, es un total corrido (FIFO contra el cronograma) -- y devuelve,
    por cada cuota, cuánto de ESA cuota específicamente sigue sin cubrirse.

    Base compartida de BR-LOAN-009 (_estado_pago_prestamo, solo mira las ya
    vencidas) y BR-LOAN-010 (GetAmortizationSchedule/RecordPayment, que
    necesitan esto para el resto del cronograma también, no solo lo vencido).

    Devuelve una lista de (fila: Cuota, monto_pendiente: Decimal, pagada: bool).
    """
    cronograma = calcular_cronograma(
        _monto_financiado(prestamo),
        prestamo.interest_rate,
        prestamo.term_months,
        fecha_primer_vencimiento=prestamo.first_due_date,
        ajustes=_ajustes_prestamo(prestamo),
    )
    _, total_pagado = _totales_prestamo(prestamo)

    disponible = total_pagado
    resultado = []
    for fila in cronograma:
        if disponible >= fila.monto_cuota:
            disponible -= fila.monto_cuota
            resultado.append((fila, CERO, True))
        else:
            pendiente = fila.monto_cuota - disponible
            disponible = CERO
            resultado.append((fila, pendiente, False))
    return resultado


def _estado_pago_prestamo(prestamo: Loan, hoy: date) -> tuple[str, Decimal, int]:
    """BR-LOAN-009: distingue un préstamo ACTIVE "al día" de uno con cuotas
    vencidas -- el status del préstamo (ACTIVE/PAID/...) no alcanza para
    saber si el cliente está atrasado. Mira solo las cuotas ya vencidas
    (fecha de vencimiento <= hoy) del resultado de _cronograma_con_pendientes
    y cuenta/suma lo que falta cubrir de esas específicamente -- a
    diferencia de `remaining_balance` (loan_service.py), que es el saldo
    total del préstamo entero, vencido o no.

    Devuelve (estado, monto_vencido, cantidad_cuotas_vencidas_impagas).
    `estado` es "" para préstamos que no sean ACTIVE (no tienen cuotas
    corriendo todavía, o el préstamo ya se resolvió).
    """
    if prestamo.status != LoanStatusEnum.ACTIVE:
        return "", CERO, 0

    monto_vencido = CERO
    cuotas_vencidas_impagas = 0
    for fila, pendiente, pagada in _cronograma_con_pendientes(prestamo):
        if fila.fecha_vencimiento is None or fila.fecha_vencimiento > hoy:
            break
        if not pagada:
            monto_vencido += pendiente
            cuotas_vencidas_impagas += 1

    estado = "CUOTA_VENCIDA" if monto_vencido > CERO else "AL_DIA"
    return estado, monto_vencido, cuotas_vencidas_impagas


def _mora_prestamo(prestamo: Loan, hoy: date) -> dict[int, MoraCuota]:
    """BR-LOAN-017: mora devengada por cada cuota vencida e impaga, a `hoy`.

    Se calcula al leer/escribir, igual que el vencimiento perezoso de
    BR-LOAN-003 (`_tal_vez_vencer_prestamo`): en este código no hay ni va a
    haber un scheduler. Y como se deriva de las fechas del cronograma en vez
    de guardarse, un préstamo que vuelve de DEFAULTED por BR-LOAN-014 recupera
    su mora solo, sin tener que reconstruir nada.

    Sólo para préstamos ACTIVE, el mismo alcance que `_estado_pago_prestamo`:
    un préstamo que todavía no se desembolsó no tiene cuotas corriendo, y uno
    ya resuelto no tiene dónde cobrarla.

    Devuelve {número de cuota: MoraCuota} y omite las cuotas sin mora, para
    que quien llama pueda preguntar por una cuota puntual sin recorrer todo.
    """
    if prestamo.status != LoanStatusEnum.ACTIVE:
        return {}

    moras: dict[int, MoraCuota] = {}
    for fila, pendiente, pagada in _cronograma_con_pendientes(prestamo):
        if fila.fecha_vencimiento is None or fila.fecha_vencimiento > hoy:
            break
        if pagada:
            continue
        mora = calcular_mora_cuota(
            fila.numero,
            pendiente,
            fila.fecha_vencimiento,
            hoy,
            tasa_mensual=config.LOAN_LATE_FEE_MONTHLY_RATE,
            dias_gracia=config.LOAN_LATE_FEE_GRACE_DAYS,
            dias_por_mes=config.LOAN_LATE_FEE_DAYS_PER_MONTH,
            desde=config.LOAN_LATE_FEE_START_DATE,
        )
        if mora.monto > CERO:
            moras[fila.numero] = mora
    return moras


def _total_mora(prestamo: Loan, hoy: date) -> Decimal:
    """Mora devengada de todo el préstamo (BR-LOAN-017).

    Distinta del `monto_vencido` de BR-LOAN-009, que es la cuota en sí: una es
    el recargo por el atraso, la otra la deuda que se atrasó.
    """
    return sum((mora.monto for mora in _mora_prestamo(prestamo, hoy).values()), CERO)


def _total_cargos(prestamo: Loan) -> Decimal:
    """BR-LOAN-006: suma de los 4 cargos/seguros del préstamo.

    **Ya no es informativa** (lo fue hasta 2026-08-28): desde que los cargos se
    capitalizan, esta suma entra en el monto que el cronograma amortiza. Ver
    `_monto_financiado`.
    """
    return sum(
        (
            cargo
            for cargo in (
                prestamo.charge_interest_tax,
                prestamo.charge_admin_fee,
                prestamo.charge_cancellation_insurance,
                prestamo.charge_contracted_insurance,
            )
            if cargo is not None
        ),
        CERO,
    )


def _monto_financiado(prestamo: Loan) -> Decimal:
    """BR-LOAN-006: "Total del Crédito" = capital solicitado + cargos.

    Es el monto que el cronograma amortiza y sobre el que se devenga el
    interés -- **no** lo que el cliente recibe en mano, que sigue siendo
    `principal_amount` ("A desembolsar"). Los cargos se financian junto con el
    capital, así que también generan interés.

    Todo lo que deriva del cronograma (saldo restante, mora de BR-LOAN-009,
    imputación FIFO de los pagos, tope de BR-LOAN-002) pasa por acá: hay una
    sola definición de qué monto se amortiza, para que la pantalla, el
    cronograma impreso y el cobro no puedan discrepar.
    """
    return prestamo.principal_amount + _total_cargos(prestamo)


# Revisado 2026-09-22. Una sola regla comercial -- "un préstamo más corto que
# un año se cobra como si durara un año" -- pero cada tope la expresa con la
# aritmética inversa del otro, y **confundirlas es un error silencioso que ya
# se cometió al implementar esto**:
#
#   * el techo de cargos se prorratea multiplicando por el plazo, así que para
#     no rendir menos que 12 meses usa `max(plazo, 12)`;
#   * la tasa se aplica mensualmente (`tasa/12` por cuota, BR-LOAN-013), así
#     que para que el interés total no baje hay que DIVIDIR por el plazo:
#     `min(plazo, 12)`.
#
# Usar `max` en los dos lados compila, pasa desapercibido y abarata los
# préstamos largos: 18 meses pasaría de 30% de interés a 20%. De ahí que sean
# dos funciones con nombres distintos y no una compartida.


def _plazo_para_cargos(plazo_meses: int) -> int:
    """Plazo con el que se prorratea el techo de cargos: `max(plazo, 12)`."""
    return max(plazo_meses, config.LOAN_RATE_MIN_TERM_MONTHS)


def _plazo_para_tasa(plazo_meses: int) -> int:
    """Plazo con el que se deriva la tasa: `min(plazo, 12)`."""
    return min(plazo_meses, config.LOAN_RATE_MIN_TERM_MONTHS)


def _tasa_vigente(plazo_meses: int) -> Decimal:
    """BR-LOAN-007 (revisado 2026-09-22): la tasa anual que corresponde a un
    plazo, derivada de `LOAN_FIXED_INTEREST_RATE`.

    `tasa_anual = LOAN_FIXED_INTEREST_RATE * 12 / min(plazo, 12)`, que es la
    tasa cuyo interés total (BR-LOAN-013: `financiado * tasa/12 * plazo`) da
    exactamente el 20% del financiado en cualquier plazo de hasta 12 meses. De
    13 meses en adelante devuelve la tasa fija de siempre, así que la cartera
    larga sigue rindiendo lo mismo que antes (30% a 18 meses, 40% a 24).

    Consecuencia visible: un préstamo a 6 meses se guarda a 0,40 anual, y el
    Pagaré declara el 3,33% mensual que de ahí se deriva. Es una decisión
    comercial de la entidad, no un descuido -- ver el comentario de
    `LOAN_RATE_MIN_TERM_MONTHS` en config.py.

    **Se cuantiza a 4 decimales a propósito**: es la precisión de
    `Loan.interest_rate` (Numeric(6,4)). Sin cuantizar acá, en los plazos que
    no dividen a 12 (7, 11) la tasa validada y la guardada difieren, y el
    cronograma que se recalcula al leer no coincide con el que se validó al
    crear. El precio de eso es que en esos plazos el interés total queda a
    centésimas del 20% exacto, no en el 20% redondo.
    """
    return (
        config.LOAN_FIXED_INTEREST_RATE
        * Decimal(12)
        / Decimal(_plazo_para_tasa(plazo_meses))
    ).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _tope_cargos(capital: Decimal, plazo_meses: int) -> Decimal:
    """BR-LOAN-006 (revisado 2026-09-22): tope conjunto de los 4 cargos
    financiados, prorrateado por el plazo con el piso de un año:
    `capital * LOAN_MAX_CHARGES_RATIO / 12 * max(plazo, 12)`.

    Con el piso, un préstamo de 6 meses admite el 40% pleno del capital en
    cargos, no el 20% que salía del prorrateo puro.

    Se mide sobre `principal_amount` (el capital solicitado), no sobre el
    monto financiado -- éste ya incluiría los cargos que el tope está
    limitando. Es el complemento de la tasa de interés para llegar al 60%
    anual que la entidad fija como costo total del crédito.
    """
    return (
        capital
        * config.LOAN_MAX_CHARGES_RATIO
        / Decimal(12)
        * _plazo_para_cargos(plazo_meses)
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _validar_tope_cargos(
    capital: Decimal, total_cargos: Decimal, plazo_meses: int, context
) -> None:
    """Aborta INVALID_ARGUMENT si los cargos superan `_tope_cargos`.

    Los 4 cargos (BR-LOAN-006) siguen siendo de monto libre -- el operador
    los escribe a mano -- pero, desde que existe un interés legal fijo
    (BR-LOAN-007, 20%) separado del costo total pactado (60%), la única
    forma de no dejar que un cargo cargado a mano exceda el 40% que le
    corresponde es validarlo acá, en el mismo punto donde ya se valida el
    tope del 40% de ingreso de BR-LOAN-002 (dos topes del 40% distintos --
    uno sobre el capital solicitado, el otro sobre el ingreso declarado del
    cliente -- que coincidan en el número es casualidad, no la misma regla).
    """
    tope = _tope_cargos(capital, plazo_meses)
    if total_cargos > tope:
        porcentaje = int(config.LOAN_MAX_CHARGES_RATIO * 100)
        context.abort(
            grpc.StatusCode.INVALID_ARGUMENT,
            "Los cargos financiados no pueden superar, en conjunto, el "
            f"{porcentaje}% anual del capital solicitado prorrateado por el "
            f"plazo ({tope} Gs para este préstamo) (BR-LOAN-006)",
        )


def _cargos_de_solicitud(request, context) -> dict[str, Decimal | None]:
    """Los 4 cargos de un CreateLoanRequest/UpdateLoanProposalRequest, ya
    convertidos a Decimal. Vacío -> None (el cargo no aplica)."""

    def _cargo(valor: str, nombre_campo: str) -> Decimal | None:
        texto = valor.strip()
        return analizar_decimal(texto, nombre_campo, context) if texto else None

    return {
        "charge_interest_tax": _cargo(
            request.charge_interest_tax, "charge_interest_tax"
        ),
        "charge_admin_fee": _cargo(request.charge_admin_fee, "charge_admin_fee"),
        "charge_cancellation_insurance": _cargo(
            request.charge_cancellation_insurance, "charge_cancellation_insurance"
        ),
        "charge_contracted_insurance": _cargo(
            request.charge_contracted_insurance, "charge_contracted_insurance"
        ),
    }


def _garantia_de_solicitud(request, context) -> tuple[str | None, Decimal | None]:
    """BR-LOAN-005: (tipo, monto) de la garantía, o (None, None) si no hay.
    Ambos campos van juntos o ninguno."""
    tipo = request.guarantee_type.strip()
    monto_texto = request.guarantee_amount.strip()
    if bool(tipo) != bool(monto_texto):
        context.abort(
            grpc.StatusCode.INVALID_ARGUMENT,
            "guarantee_type y guarantee_amount deben completarse juntos, "
            "o dejarse ambos vacíos (BR-LOAN-005)",
        )
    monto = (
        analizar_decimal(monto_texto, "guarantee_amount", context)
        if monto_texto
        else None
    )
    return (tipo or None), monto


def _tasa_estandar_o_abortar(texto_tasa: str, plazo_meses: int, context) -> Decimal:
    """BR-LOAN-007: la tasa es fija para todos los roles, sin excepción.

    "Fija" no quiere decir "única": desde 2026-09-22 la tasa la determina el
    plazo (`_tasa_vigente`), no el operador. Lo que sigue sin poder elegirse
    es *qué* tasa se aplica a un préstamo dado.

    Se acepta el campo vacío -- la forma normal de pedir "la tasa vigente" y
    lo que manda el cliente desde que se le sacó el campo del formulario -- o
    exactamente la tasa que corresponde a ese plazo, para que un llamador
    viejo que la manda explícitamente siga funcionando. Cualquier otro valor
    se rechaza en vez de descartarse en silencio: si alguien pidió 30%,
    dejarlo creado a la tasa fija sin avisar es peor que fallar.

    Hasta 2026-08-28 un MANAGER/ADMIN podía fijar una tasa distinta acá. Ya no:
    la tasa pasó a ser una decisión comercial de la entidad y se cambia en
    `config.LOAN_FIXED_INTEREST_RATE`/`LOAN_RATE_MIN_TERM_MONTHS` (mirroreadas
    a mano en `cas_client/`), no préstamo por préstamo.
    """
    estandar = _tasa_vigente(plazo_meses)
    texto = texto_tasa.strip()
    if not texto:
        return estandar
    tasa = analizar_decimal(texto, "interest_rate", context)
    if tasa != estandar:
        context.abort(
            grpc.StatusCode.FAILED_PRECONDITION,
            f"La tasa de interés de un préstamo a {plazo_meses} meses es fija "
            f"({estandar}) y no puede cambiarse desde la aplicación "
            "(BR-LOAN-007)",
        )
    return tasa


def _validar_tope_cuota(
    cliente: Client,
    monto_financiado: Decimal,
    tasa: Decimal,
    plazo_meses: int,
    context,
) -> None:
    """BR-LOAN-002: la cuota no puede exceder el 40% del ingreso declarado.

    Se mide contra `cronograma[0]`, la cuota representativa del sistema alemán
    (BR-LOAN-013: todas iguales salvo los centavos de la última), y sobre el
    **monto financiado** -- capital + cargos capitalizados, que es lo que el
    cliente realmente va a pagar todos los meses.
    """
    if cliente.declared_monthly_income is None:
        context.abort(
            grpc.StatusCode.FAILED_PRECONDITION,
            "El cliente no tiene ingresos declarados registrados (BR-LOAN-002)",
        )
    cuota_mensual = calcular_cronograma(monto_financiado, tasa, plazo_meses)[
        0
    ].monto_cuota
    cuota_maxima = (
        cliente.declared_monthly_income * config.LOAN_MAX_INSTALLMENT_INCOME_RATIO
    )
    if cuota_mensual > cuota_maxima:
        context.abort(
            grpc.StatusCode.FAILED_PRECONDITION,
            "La cuota mensual excede el 40% del ingreso declarado (BR-LOAN-002)",
        )


def _nombre_usuario_creador(sesion, prestamo: Loan) -> str:
    """Username de quien registró el préstamo (CreateLoan), o "" si no se
    conoce -- ver el comentario de Loan.created_by_user_id en models.py."""
    if prestamo.created_by_user_id is None:
        return ""
    creador = sesion.get(User, prestamo.created_by_user_id)
    return creador.username if creador is not None else ""


def _datos_personales_creador(sesion, prestamo: Loan) -> tuple[str, str]:
    """(nombre completo, C.I.) del asesor que registró el préstamo
    (BR-AUTH-006), o ("", "") si no se conoce o el usuario no tiene datos
    personales cargados. El documento decide qué mostrar cuando viene vacío
    -- acá no se sustituye por el username, para no hacer pasar un nombre de
    usuario por un nombre real."""
    if prestamo.created_by_user_id is None:
        return "", ""
    creador = sesion.get(User, prestamo.created_by_user_id)
    if creador is None:
        return "", ""
    return _nombre_completo(creador), creador.national_id or ""


def _nombre_completo(usuario: User) -> str:
    """ "Nombre Apellido" del operador, o "" si no tiene ambos cargados."""
    if not usuario.first_name or not usuario.last_name:
        return ""
    return f"{usuario.first_name} {usuario.last_name}"


def _datos_de_operador(usuario: User | None) -> tuple[str, str]:
    """(nombre para mostrar, C.I.) de un operador guardado, o ("", "") si no
    hay ninguno.

    Un pago anterior a BR-LOAN-016 no tiene responsable: devuelve ("", "") y
    el comprobante reimpreso lo dice, en vez de atribuirle el cobro a quien
    está consultando el historial.
    """
    if usuario is None:
        return "", ""
    return _nombre_completo(usuario) or usuario.username, usuario.national_id or ""


def _operador_actual(sesion) -> tuple[str, str]:
    """(nombre para mostrar, C.I.) del operador autenticado que está haciendo
    la llamada -- para el "Registrado por" del Comprobante de Pago.

    Cae de vuelta al `username` cuando el usuario no tiene nombre/apellido
    cargados (usuarios anteriores a BR-AUTH-006), de modo que el comprobante
    nunca sale con el campo en blanco. Devuelve ("", "") cuando no hay
    credenciales -- los tests que llaman al servicer directamente, sin
    AuthInterceptor, caen acá; mismo patrón tolerante a None que
    id_actor_actual.
    """
    credenciales = get_current_claims()
    if credenciales is None:
        return "", ""
    usuario = sesion.get(User, uuid.UUID(credenciales.user_id))
    if usuario is None:
        return credenciales.username, ""
    return _datos_de_operador(usuario)


def _cuotas_cubiertas_por_pago(
    prestamo: Loan, pagado_antes: Decimal, monto: Decimal
) -> list[int]:
    """BR-LOAN-011: números de cuota que cubre un pago de `monto` cuando ya
    había `pagado_antes` acumulado.

    Los pagos no están atados a una cuota puntual en el modelo (LoanPayment es
    un total corrido que se imputa FIFO contra el cronograma, ver
    _cronograma_con_pendientes), así que "qué cuota pagó" se deduce de qué
    tramo del cronograma cae dentro del intervalo acumulado
    [pagado_antes, pagado_antes + monto).

    Esto es a propósito la MISMA imputación que usan BR-LOAN-009 y el
    cronograma para marcar cuotas como pagadas: si el comprobante dijera otra
    cosa (p. ej. la cuota que el operador eligió en el desplegable, cuando
    quedaban cuotas anteriores impagas), contradiría lo que muestra la propia
    pantalla del préstamo justo después.

    Un pago que no llega a cubrir ninguna cuota entera igual devuelve la cuota
    que abonó parcialmente -- el comprobante dice a qué cuota se imputó, no
    cuáles quedaron saldadas.
    """
    cronograma = calcular_cronograma(
        _monto_financiado(prestamo),
        prestamo.interest_rate,
        prestamo.term_months,
        fecha_primer_vencimiento=prestamo.first_due_date,
        ajustes=_ajustes_prestamo(prestamo),
    )
    hasta = pagado_antes + monto
    cubiertas: list[int] = []
    inicio_cuota = CERO
    for fila in cronograma:
        fin_cuota = inicio_cuota + fila.monto_cuota
        # Intersección no vacía entre [inicio_cuota, fin_cuota) y
        # [pagado_antes, hasta).
        if inicio_cuota < hasta and fin_cuota > pagado_antes:
            cubiertas.append(fila.numero)
        inicio_cuota = fin_cuota
    return cubiertas


def _cuotas_saldadas_por_pago(
    prestamo: Loan, pagado_antes: Decimal, monto: Decimal
) -> list[int]:
    """Cuotas que este pago deja **enteramente** cubiertas.

    Subconjunto de `_cuotas_cubiertas_por_pago`, que también devuelve la cuota
    abonada a medias. La distinción importa sólo para la mora (BR-LOAN-017):
    se cobra el recargo de las cuotas que quedan saldadas, no el de una que
    sigue impaga. Si se cobrara el de una cuota parcialmente cubierta, esa
    cuota seguiría devengando sobre los mismos días ya cobrados y el deudor
    pagaría dos veces el mismo atraso.

    El costo de esa decisión es que un pago libre que no alcanza a saldar la
    cuota no paga mora todavía: se cobra entera cuando la cuota se completa,
    calculada sobre el saldo que quedaba. Conservador a favor del deudor, y
    exacto en el camino normal (BR-LOAN-010), donde la cuota elegida siempre
    se cubre entera.
    """
    cronograma = calcular_cronograma(
        _monto_financiado(prestamo),
        prestamo.interest_rate,
        prestamo.term_months,
        fecha_primer_vencimiento=prestamo.first_due_date,
        ajustes=_ajustes_prestamo(prestamo),
    )
    hasta = pagado_antes + monto
    saldadas: list[int] = []
    inicio_cuota = CERO
    for fila in cronograma:
        fin_cuota = inicio_cuota + fila.monto_cuota
        if fin_cuota > pagado_antes and fin_cuota <= hasta:
            saldadas.append(fila.numero)
        inicio_cuota = fin_cuota
    return saldadas


def _prestamo_a_respuesta(
    prestamo: Loan, sesion
) -> loan_service_pb2.GetLoanByIdResponse:
    total_programado, total_pagado = _totales_prestamo(prestamo)
    saldo_restante = max(total_programado - total_pagado, CERO)
    total_cargos = _total_cargos(prestamo)
    monto_financiado = _monto_financiado(prestamo)
    cronograma = calcular_cronograma(
        monto_financiado,
        prestamo.interest_rate,
        prestamo.term_months,
        ajustes=_ajustes_prestamo(prestamo),
    )
    total_interes = sum((fila.interes for fila in cronograma), CERO)
    hoy = datetime.now(timezone.utc).date()
    estado_pago, monto_vencido, cuotas_vencidas = _estado_pago_prestamo(prestamo, hoy)
    mora_devengada = _total_mora(prestamo, hoy)
    argumentos = dict(
        id=str(prestamo.id),
        client_id=str(prestamo.client_id),
        principal_amount=str(prestamo.principal_amount),
        interest_rate=str(prestamo.interest_rate),
        term_months=prestamo.term_months,
        status=prestamo.status.value,
        created_at=a_marca_tiempo(prestamo.created_at),
        total_paid=str(total_pagado),
        remaining_balance=str(saldo_restante),
        # BR-LOAN-018: total_paid incluye lo condonado; la ficha lo desglosa.
        total_discount=str(
            sum((pago.discount_amount or CERO for pago in prestamo.payments), CERO)
        ),
        first_due_date=prestamo.first_due_date.isoformat(),
        guarantee_type=prestamo.guarantee_type or "",
        guarantee_amount=(
            "" if prestamo.guarantee_amount is None else str(prestamo.guarantee_amount)
        ),
        charge_interest_tax=(
            ""
            if prestamo.charge_interest_tax is None
            else str(prestamo.charge_interest_tax)
        ),
        charge_admin_fee=(
            "" if prestamo.charge_admin_fee is None else str(prestamo.charge_admin_fee)
        ),
        charge_cancellation_insurance=(
            ""
            if prestamo.charge_cancellation_insurance is None
            else str(prestamo.charge_cancellation_insurance)
        ),
        charge_contracted_insurance=(
            ""
            if prestamo.charge_contracted_insurance is None
            else str(prestamo.charge_contracted_insurance)
        ),
        total_charges=str(total_cargos),
        # "Total del Crédito" de la propuesta: capital + cargos, el monto que
        # el cronograma amortiza (BR-LOAN-006). Distinto de total_to_pay, que
        # le suma además el interés.
        total_credit_with_charges=str(monto_financiado),
        amount_to_disburse=str(prestamo.principal_amount),
        total_interest=str(total_interes),
        total_to_pay=str(total_programado),
        installment_amount=str(cronograma[0].monto_cuota),
        payment_status=estado_pago,
        overdue_amount=str(monto_vencido),
        overdue_installments_count=cuotas_vencidas,
        # BR-LOAN-017. Va al lado de overdue_amount y no sumado a él a
        # propósito: uno es la cuota que se atrasó, el otro el recargo por
        # haberse atrasado, y la pantalla los muestra separados por lo mismo.
        accrued_late_fee=str(mora_devengada),
        created_by_username=_nombre_usuario_creador(sesion, prestamo),
    )
    nombre_asesor, ci_asesor = _datos_personales_creador(sesion, prestamo)
    argumentos["created_by_full_name"] = nombre_asesor
    argumentos["created_by_national_id"] = ci_asesor
    if prestamo.approved_at is not None:
        argumentos["approved_at"] = a_marca_tiempo(prestamo.approved_at)
    return loan_service_pb2.GetLoanByIdResponse(**argumentos)


class LoanServicer(loan_service_pb2_grpc.LoanServiceServicer):
    def CreateLoan(self, request, context):
        """Alta de la propuesta completa: términos, garantía y cargos juntos.

        Los cargos entran acá y no en una segunda llamada porque desde
        BR-LOAN-006 se capitalizan: forman parte del monto que se amortiza, así
        que determinan la cuota que BR-LOAN-002 tiene que validar. Crear el
        préstamo sin ellos y agregarlos después habría dejado pasar propuestas
        cuya cuota real excede el tope.
        """
        if not (request.client_id and request.principal_amount):
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "client_id y principal_amount son obligatorios",
            )
        if request.term_months <= 0:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT, "term_months debe ser positivo"
            )

        client_id = analizar_uuid(request.client_id, "client_id", context)
        capital = analizar_decimal(
            request.principal_amount, "principal_amount", context, permitir_cero=False
        )
        tasa_interes = _tasa_estandar_o_abortar(
            request.interest_rate, request.term_months, context
        )
        tipo_garantia, monto_garantia = _garantia_de_solicitud(request, context)
        cargos = _cargos_de_solicitud(request, context)
        total_cargos = sum((c for c in cargos.values() if c is not None), CERO)
        _validar_tope_cargos(capital, total_cargos, request.term_months, context)

        claims = get_current_claims()
        ahora = datetime.now(timezone.utc)
        fecha_primer_vencimiento = (
            analizar_fecha(request.first_due_date, "first_due_date", context)
            if request.first_due_date.strip()
            else ahora.date() + timedelta(days=config.LOAN_DEFAULT_FIRST_DUE_DAYS)
        )

        with SessionLocal() as sesion:
            # BR-LOAN-001: bloquea la fila del cliente (FOR UPDATE) desde acá
            # hasta el commit, así dos CreateLoan concurrentes para el mismo
            # cliente quedan serializados en vez de ambos leer el mismo
            # conteo de préstamos activos antes de que cualquiera confirme.
            # Ver ES-006 §3.1.
            cliente = sesion.get(Client, client_id, with_for_update=True)
            if cliente is None:
                context.abort(grpc.StatusCode.NOT_FOUND, "Cliente no encontrado")
            if not cliente.is_active:
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION, "El cliente no está activo"
                )

            _validar_tope_cuota(
                cliente,
                capital + total_cargos,
                tasa_interes,
                request.term_months,
                context,
            )

            prestamos_existentes = (
                sesion.query(Loan).filter(Loan.client_id == cliente.id).all()
            )
            cantidad_activos, vencidos = _vencer_atrasados_y_contar_activos(
                prestamos_existentes, ahora
            )
            _auditar_vencidos(sesion, vencidos, context, ahora)
            if cantidad_activos >= config.LOAN_MAX_ACTIVE_PER_CLIENT:
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "El cliente ya tiene el número máximo de préstamos "
                    "activos (BR-LOAN-001)",
                )

            prestamo = Loan(
                client_id=cliente.id,
                created_by_user_id=id_actor_actual(claims),
                principal_amount=capital,
                interest_rate=tasa_interes,
                term_months=request.term_months,
                first_due_date=fecha_primer_vencimiento,
                guarantee_type=tipo_garantia,
                guarantee_amount=monto_garantia,
                status=LoanStatusEnum.PENDING,
                created_at=ahora,
                **cargos,
            )
            sesion.add(prestamo)
            sesion.flush()

            sesion.add(
                AuditLog(
                    user_id=id_actor_actual(get_current_claims()),
                    action=(
                        f"PRESTAMO_CREADO loan_id={prestamo.id} "
                        f"client_id={cliente.id} capital={capital} "
                        f"cargos={total_cargos}"
                    ),
                    ip_address=ip_remota(context),
                    timestamp=ahora,
                )
            )
            sesion.commit()

            return loan_service_pb2.CreateLoanResponse(
                loan_id=str(prestamo.id),
                status=prestamo.status.value,
                created_at=a_marca_tiempo(ahora),
            )

    def UpdateLoanProposal(self, request, context):
        loan_id = analizar_uuid(request.loan_id, "loan_id", context)
        if not request.principal_amount or not request.first_due_date:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "principal_amount y first_due_date son obligatorios",
            )
        if request.term_months <= 0:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT, "term_months debe ser positivo"
            )
        capital = analizar_decimal(
            request.principal_amount, "principal_amount", context, permitir_cero=False
        )
        fecha_primer_vencimiento = analizar_fecha(
            request.first_due_date, "first_due_date", context
        )
        tipo_garantia, monto_garantia = _garantia_de_solicitud(request, context)
        cargos = _cargos_de_solicitud(request, context)
        total_cargos = sum((c for c in cargos.values() if c is not None), CERO)
        _validar_tope_cargos(capital, total_cargos, request.term_months, context)

        with SessionLocal() as sesion:
            prestamo = sesion.get(Loan, loan_id)
            if prestamo is None:
                context.abort(grpc.StatusCode.NOT_FOUND, "Préstamo no encontrado")

            if prestamo.status != LoanStatusEnum.PENDING:
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "Solo se puede editar una propuesta en estado PENDING "
                    f"(estado actual: {prestamo.status.value}) (BR-LOAN-004)",
                )

            if fecha_primer_vencimiento < prestamo.created_at.date():
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    "first_due_date no puede ser anterior a la fecha de "
                    "creación del préstamo",
                )

            # Misma verificación que CreateLoan hace antes de validar
            # BR-LOAN-002. Faltaba acá: sin ella, un cliente sin ingresos
            # declarados hacía reventar la multiplicación del tope
            # (None * Decimal) y la RPC respondía UNKNOWN -- que la vista
            # traduce a "Ocurrió un error inesperado", sin decir qué falta.
            cliente = sesion.get(Client, prestamo.client_id)
            if cliente is None:
                context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    "El cliente del préstamo no existe",
                )

            # BR-LOAN-007: la tasa depende del plazo desde 2026-09-22, así que
            # editar el plazo obliga a re-tasar. Es la **única** excepción a
            # "UpdateLoanProposal no toca interest_rate": sin ella, bajar una
            # propuesta de 12 a 6 meses la dejaría cobrando la mitad de
            # interés en silencio, que es justo lo que el piso de plazo vino a
            # evitar. Sigue sin poder elegirse la tasa: se recalcula, no se
            # recibe.
            tasa_anterior = prestamo.interest_rate
            tasa_interes = _tasa_vigente(request.term_months)

            # El tope se mide sobre capital + cargos: los cargos se capitalizan
            # (BR-LOAN-006), así que suben la cuota igual que el capital.
            _validar_tope_cuota(
                cliente,
                capital + total_cargos,
                tasa_interes,
                request.term_months,
                context,
            )

            monto_anterior = prestamo.principal_amount
            cuotas_anterior = prestamo.term_months
            cargos_anterior = _total_cargos(prestamo)
            prestamo.principal_amount = capital
            prestamo.term_months = request.term_months
            prestamo.interest_rate = tasa_interes
            prestamo.first_due_date = fecha_primer_vencimiento
            prestamo.guarantee_type = tipo_garantia
            prestamo.guarantee_amount = monto_garantia
            for campo, valor in cargos.items():
                setattr(prestamo, campo, valor)

            sesion.add(
                AuditLog(
                    user_id=id_actor_actual(get_current_claims()),
                    action=(
                        f"PRESTAMO_PROPUESTA_ACTUALIZADA loan_id={prestamo.id} "
                        f"monto_anterior={monto_anterior} monto_nuevo={capital} "
                        f"cuotas_anterior={cuotas_anterior} "
                        f"cuotas_nuevo={request.term_months} "
                        f"tasa_anterior={tasa_anterior} tasa_nueva={tasa_interes} "
                        f"cargos_anterior={cargos_anterior} "
                        f"cargos_nuevo={total_cargos} "
                        f"garantia={tipo_garantia or '-'}/{monto_garantia}"
                    ),
                    ip_address=ip_remota(context),
                    timestamp=datetime.now(timezone.utc),
                )
            )
            sesion.commit()

            return loan_service_pb2.UpdateLoanProposalResponse(
                success=True, status=prestamo.status.value
            )

    def UpdateLoanGuarantee(self, request, context):
        loan_id = analizar_uuid(request.loan_id, "loan_id", context)
        tipo, monto = _garantia_de_solicitud(request, context)

        with SessionLocal() as sesion:
            prestamo = sesion.get(Loan, loan_id)
            if prestamo is None:
                context.abort(grpc.StatusCode.NOT_FOUND, "Préstamo no encontrado")

            if prestamo.status != LoanStatusEnum.PENDING:
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "Solo se puede editar la garantía de una propuesta en "
                    f"estado PENDING (estado actual: {prestamo.status.value}) "
                    "(BR-LOAN-005)",
                )

            prestamo.guarantee_type = tipo
            prestamo.guarantee_amount = monto

            sesion.add(
                AuditLog(
                    user_id=id_actor_actual(get_current_claims()),
                    action=(
                        f"PRESTAMO_GARANTIA_ACTUALIZADA loan_id={prestamo.id} "
                        f"tipo={tipo or '-'} monto={monto}"
                    ),
                    ip_address=ip_remota(context),
                    timestamp=datetime.now(timezone.utc),
                )
            )
            sesion.commit()

            return loan_service_pb2.UpdateLoanGuaranteeResponse(
                success=True, status=prestamo.status.value
            )

    def UpdateLoanCharges(self, request, context):
        loan_id = analizar_uuid(request.loan_id, "loan_id", context)

        with SessionLocal() as sesion:
            prestamo = sesion.get(Loan, loan_id)
            if prestamo is None:
                context.abort(grpc.StatusCode.NOT_FOUND, "Préstamo no encontrado")

            if prestamo.status != LoanStatusEnum.PENDING:
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "Solo se pueden editar los cargos de una propuesta en "
                    f"estado PENDING (estado actual: {prestamo.status.value}) "
                    "(BR-LOAN-006)",
                )

            cargos = _cargos_de_solicitud(request, context)
            total_cargos = sum((c for c in cargos.values() if c is not None), CERO)
            _validar_tope_cargos(
                prestamo.principal_amount, total_cargos, prestamo.term_months, context
            )

            # Desde que los cargos se capitalizan (BR-LOAN-006) suben la cuota,
            # así que esta RPC pasó a ser un camino más por el que se puede
            # violar el tope del 40%. Antes no hacía falta validar acá porque
            # los cargos no tocaban el cronograma.
            cliente = sesion.get(Client, prestamo.client_id)
            if cliente is None:
                context.abort(
                    grpc.StatusCode.NOT_FOUND, "El cliente del préstamo no existe"
                )
            _validar_tope_cuota(
                cliente,
                prestamo.principal_amount + total_cargos,
                prestamo.interest_rate,
                prestamo.term_months,
                context,
            )

            for campo, valor in cargos.items():
                setattr(prestamo, campo, valor)

            sesion.add(
                AuditLog(
                    user_id=id_actor_actual(get_current_claims()),
                    action=(
                        f"PRESTAMO_CARGOS_ACTUALIZADOS loan_id={prestamo.id} "
                        f"impuesto_interes={prestamo.charge_interest_tax} "
                        f"gastos_administrativos={prestamo.charge_admin_fee} "
                        f"seguro_cancelacion={prestamo.charge_cancellation_insurance} "
                        f"seguros_contratados={prestamo.charge_contracted_insurance}"
                    ),
                    ip_address=ip_remota(context),
                    timestamp=datetime.now(timezone.utc),
                )
            )
            sesion.commit()

            return loan_service_pb2.UpdateLoanChargesResponse(
                success=True,
                status=prestamo.status.value,
                total_charges=str(_total_cargos(prestamo)),
            )

    def GetLoanById(self, request, context):
        loan_id = analizar_uuid(request.loan_id, "loan_id", context)
        ahora = datetime.now(timezone.utc)

        with SessionLocal() as sesion:
            prestamo = sesion.get(Loan, loan_id)
            if prestamo is None:
                context.abort(grpc.StatusCode.NOT_FOUND, "Préstamo no encontrado")

            if _tal_vez_vencer_prestamo(prestamo, ahora):
                _auditar_vencidos(sesion, [prestamo], context, ahora)
                sesion.commit()

            return _prestamo_a_respuesta(prestamo, sesion)

    def ListClientLoans(self, request, context):
        client_id = analizar_uuid(request.client_id, "client_id", context)
        ahora = datetime.now(timezone.utc)

        with SessionLocal() as sesion:
            cliente = sesion.get(Client, client_id)
            if cliente is None:
                context.abort(grpc.StatusCode.NOT_FOUND, "Cliente no encontrado")

            prestamos = sesion.query(Loan).filter(Loan.client_id == client_id).all()
            _, vencidos = _vencer_atrasados_y_contar_activos(prestamos, ahora)
            if vencidos:
                _auditar_vencidos(sesion, vencidos, context, ahora)
                sesion.commit()

            return loan_service_pb2.ListClientLoansResponse(
                loans=[
                    _prestamo_a_respuesta(prestamo, sesion) for prestamo in prestamos
                ]
            )

    def ListActiveLoans(self, request, context):
        """Vista consolidada de cartera activa (no filtrada por cliente) --
        pedida para poder ver de un vistazo todos los préstamos ACTIVE sin
        tener que buscarlos cliente por cliente."""
        ahora = datetime.now(timezone.utc)

        with SessionLocal() as sesion:
            prestamos = (
                sesion.query(Loan)
                .filter(Loan.status == LoanStatusEnum.ACTIVE)
                .order_by(Loan.created_at)
                .all()
            )
            resultados = []
            for prestamo in prestamos:
                cliente = sesion.get(Client, prestamo.client_id)
                total_programado, total_pagado = _totales_prestamo(prestamo)
                saldo_restante = max(total_programado - total_pagado, CERO)
                estado_pago, monto_vencido, cuotas_vencidas = _estado_pago_prestamo(
                    prestamo, ahora.date()
                )
                resultados.append(
                    loan_service_pb2.ActiveLoanSummary(
                        id=str(prestamo.id),
                        client_id=str(prestamo.client_id),
                        client_name=(
                            f"{cliente.first_name} {cliente.last_name}"
                            if cliente is not None
                            else ""
                        ),
                        principal_amount=str(prestamo.principal_amount),
                        interest_rate=str(prestamo.interest_rate),
                        term_months=prestamo.term_months,
                        remaining_balance=str(saldo_restante),
                        payment_status=estado_pago,
                        overdue_amount=str(monto_vencido),
                        overdue_installments_count=cuotas_vencidas,
                        first_due_date=prestamo.first_due_date.isoformat(),
                    )
                )
            return loan_service_pb2.ListActiveLoansResponse(loans=resultados)

    def ApproveLoan(self, request, context):
        loan_id = analizar_uuid(request.loan_id, "loan_id", context)
        ahora = datetime.now(timezone.utc)

        with SessionLocal() as sesion:
            # Se bloquea la fila del préstamo (FOR UPDATE) para serializar
            # ApproveLoan concurrentes sobre el mismo loan_id: sin esto, dos
            # llamadas simultáneas pueden leer ambas status==PENDING antes de
            # que cualquiera confirme, y las dos terminan aprobando el mismo
            # préstamo. Ver ES-006 §3.1.
            prestamo = sesion.get(Loan, loan_id, with_for_update=True)
            if prestamo is None:
                context.abort(grpc.StatusCode.NOT_FOUND, "Préstamo no encontrado")

            if prestamo.status != LoanStatusEnum.PENDING:
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "El préstamo no está en estado PENDING",
                )

            # BR-LOAN-001: un cliente puede acumular préstamos PENDING sin
            # límite (no cuentan para el tope), así que la aprobación debe
            # volver a verificarlo aquí también. Se bloquea la fila del
            # cliente (FOR UPDATE) para serializar con CreateLoan/ApproveLoan
            # concurrentes del mismo cliente -- mismo mecanismo que CreateLoan
            # usa para BR-LOAN-001. Ver ES-006 §3.1.
            sesion.get(Client, prestamo.client_id, with_for_update=True)
            otros_prestamos = (
                sesion.query(Loan)
                .filter(Loan.client_id == prestamo.client_id, Loan.id != prestamo.id)
                .all()
            )
            cantidad_activos, vencidos = _vencer_atrasados_y_contar_activos(
                otros_prestamos, ahora
            )
            _auditar_vencidos(sesion, vencidos, context, ahora)
            if cantidad_activos >= config.LOAN_MAX_ACTIVE_PER_CLIENT:
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "El cliente ya tiene el número máximo de préstamos "
                    "activos (BR-LOAN-001)",
                )

            prestamo.status = LoanStatusEnum.APPROVED
            prestamo.approved_at = ahora

            sesion.add(
                AuditLog(
                    user_id=id_actor_actual(get_current_claims()),
                    action=f"PRESTAMO_APROBADO loan_id={prestamo.id}",
                    ip_address=ip_remota(context),
                    timestamp=ahora,
                )
            )
            sesion.commit()

            return loan_service_pb2.ApproveLoanResponse(
                success=True,
                status=prestamo.status.value,
                approved_at=a_marca_tiempo(prestamo.approved_at),
            )

    def DisburseLoan(self, request, context):
        loan_id = analizar_uuid(request.loan_id, "loan_id", context)
        ahora = datetime.now(timezone.utc)

        with SessionLocal() as sesion:
            prestamo = sesion.get(Loan, loan_id)
            if prestamo is None:
                context.abort(grpc.StatusCode.NOT_FOUND, "Préstamo no encontrado")

            if _tal_vez_vencer_prestamo(prestamo, ahora):
                _auditar_vencidos(sesion, [prestamo], context, ahora)
                sesion.commit()

            if prestamo.status != LoanStatusEnum.APPROVED:
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "El préstamo no está en estado APPROVED "
                    f"(estado actual: {prestamo.status.value})",
                )

            prestamo.status = LoanStatusEnum.ACTIVE
            # BR-DASH-002: el status dice que está desembolsado, pero no
            # cuándo -- y sin la fecha no hay forma de totalizar lo desembolsado
            # en un período. Se guarda el mismo `ahora` que va al AuditLog para
            # que las dos fuentes no puedan diferir en unos milisegundos.
            prestamo.disbursed_at = ahora

            sesion.add(
                AuditLog(
                    user_id=id_actor_actual(get_current_claims()),
                    action=f"PRESTAMO_DESEMBOLSADO loan_id={prestamo.id}",
                    ip_address=ip_remota(context),
                    timestamp=ahora,
                )
            )
            sesion.commit()

            return loan_service_pb2.DisburseLoanResponse(
                success=True, status=prestamo.status.value
            )

    def RecordPayment(self, request, context):
        loan_id = analizar_uuid(request.loan_id, "loan_id", context)
        # BR-CAJA-004: el medio de pago vuelve a admitir efectivo. Vacío se
        # lee como TRANSFERENCIA para que los clientes anteriores a la caja
        # (y los tests que ya existían) sigan funcionando sin cambios.
        medio_texto = request.payment_method.strip().upper()
        try:
            medio_pago = (
                PaymentMethodEnum(medio_texto)
                if medio_texto
                else PaymentMethodEnum.TRANSFERENCIA
            )
        except ValueError:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "payment_method debe ser EFECTIVO o TRANSFERENCIA",
            )

        es_efectivo = medio_pago == PaymentMethodEnum.EFECTIVO
        # En transferencia/descuento la referencia es el único rastro del
        # cobro, así que es obligatoria. En efectivo la trazabilidad la da el
        # movimiento de caja que se genera más abajo, y no hay número que
        # pedir -- exigir uno solo llevaría a inventarlo.
        referencia_transferencia = request.transfer_reference.strip()
        if not es_efectivo and not referencia_transferencia:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT, "transfer_reference es obligatorio"
            )
        if es_efectivo:
            referencia_transferencia = ""

        # A diferencia del resto de este servicer, el cobro en efectivo no
        # tolera `claims is None`: sin operador autenticado no hay caja a la
        # cual imputarlo, y registrar el pago igual dejaría el efectivo fuera
        # de todo arqueo.
        credenciales_efectivo = get_current_claims() if es_efectivo else None
        if es_efectivo and credenciales_efectivo is None:
            context.abort(
                grpc.StatusCode.UNAUTHENTICATED,
                "El cobro en efectivo requiere un usuario autenticado con caja abierta",
            )

        # BR-LOAN-010: si se especifica una cuota puntual, el monto se
        # recalcula acá mismo a partir del cronograma -- nunca se confía en
        # un `amount` que mande el cliente para ese caso, así el monto
        # registrado siempre es el fijo que corresponde a esa cuota.
        #
        # BR-LOAN-018: la cancelación total tampoco confía en el cliente --
        # el saldo lo calcula el servidor con la fila bloqueada, y `amount` e
        # `installment_number` se ignoran. Lo único que decide el operador es
        # el descuento, y ese se valida contra el saldo más abajo.
        pago_total = request.pay_in_full
        descuento_texto = request.discount_amount.strip()
        if descuento_texto and not pago_total:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "El descuento sólo se admite al cancelar el préstamo en un solo pago",
            )
        descuento = (
            analizar_decimal(descuento_texto, "discount_amount", context).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            if descuento_texto
            else CERO
        )

        numero_cuota = 0 if pago_total else request.installment_number
        usa_cuota_fija = numero_cuota > 0
        if not pago_total and not usa_cuota_fija and not request.amount:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "amount es obligatorio")
        monto = (
            None
            if usa_cuota_fija or pago_total
            else analizar_decimal(
                request.amount, "amount", context, permitir_cero=False
            )
        )
        ahora = datetime.now(timezone.utc)

        with SessionLocal() as sesion:
            # Bloquea la fila del préstamo (FOR UPDATE) para serializar pagos
            # concurrentes sobre el mismo préstamo -- sin esto, dos pagos que
            # en conjunto saldan el préstamo pero que individualmente no ven
            # el total combinado podrían dejarlo sin pasar a PAID. Ver
            # ES-006 §3.1.
            prestamo = sesion.get(Loan, loan_id, with_for_update=True)
            if prestamo is None:
                context.abort(grpc.StatusCode.NOT_FOUND, "Préstamo no encontrado")

            if prestamo.status != LoanStatusEnum.ACTIVE:
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION, "El préstamo no está activo"
                )

            if usa_cuota_fija:
                fila_objetivo = next(
                    (
                        (fila, pendiente, pagada)
                        for fila, pendiente, pagada in _cronograma_con_pendientes(
                            prestamo
                        )
                        if fila.numero == numero_cuota
                    ),
                    None,
                )
                if fila_objetivo is None:
                    context.abort(
                        grpc.StatusCode.INVALID_ARGUMENT,
                        f"La cuota {numero_cuota} no existe para este préstamo",
                    )
                _, pendiente, pagada = fila_objetivo
                if pagada:
                    context.abort(
                        grpc.StatusCode.FAILED_PRECONDITION,
                        f"La cuota {numero_cuota} ya está saldada",
                    )
                monto = pendiente

            total_programado, total_pagado = _totales_prestamo(prestamo)

            if pago_total:
                # BR-LOAN-018: se imputa el saldo ENTERO, no el neto del
                # descuento. Es lo que deja el préstamo en PAID por el mismo
                # camino que cualquier otro cobro, y lo que hace que la
                # imputación FIFO (comprobante, historial, mora) cubra todas
                # las cuotas pendientes sin un caso especial.
                monto = total_programado - total_pagado
                if monto <= CERO:
                    context.abort(
                        grpc.StatusCode.FAILED_PRECONDITION,
                        "El préstamo no tiene saldo pendiente",
                    )
                # Tiene que quedar algo del saldo por cobrar: condonarlo
                # entero sería castigar la deuda, no cobrarla. La mora nunca
                # entra en el descuento (se cobra completa, BR-LOAN-017).
                if descuento >= monto:
                    context.abort(
                        grpc.StatusCode.INVALID_ARGUMENT,
                        f"El descuento debe ser menor que el saldo pendiente "
                        f"({monto} Gs)",
                    )

            nuevo_total_pagado = total_pagado + monto

            # BR-LOAN-017: la mora del atraso se cobra junto con la cuota, y
            # el operador no puede cobrar de menos -- el servidor fija el
            # total, igual que fija el monto de la cuota en BR-LOAN-010. Se
            # calcula con el acumulado ANTERIOR a este pago (como la
            # imputación del comprobante) y sólo sobre las cuotas que este
            # pago deja saldadas.
            moras = _mora_prestamo(prestamo, ahora.date())
            mora_cobrada = sum(
                (
                    moras[numero].monto
                    for numero in _cuotas_saldadas_por_pago(
                        prestamo, total_pagado, monto
                    )
                    if numero in moras
                ),
                CERO,
            )

            # BR-CAJA-004: la caja se busca (y se bloquea) DESPUÉS del
            # préstamo, manteniendo el mismo orden de bloqueo en todos los
            # caminos que tocan ambas filas, y ANTES de insertar el pago, para
            # no dejar un LoanPayment registrado si resulta que no hay caja
            # abierta donde imputarlo.
            sesion_caja = None
            if es_efectivo:
                sesion_caja = obtener_sesion_abierta(
                    sesion,
                    uuid.UUID(credenciales_efectivo.user_id),
                    bloquear=True,
                )
                if sesion_caja is None:
                    context.abort(
                        grpc.StatusCode.FAILED_PRECONDITION,
                        "No hay una caja abierta para registrar un cobro en efectivo",
                    )

            pago = LoanPayment(
                loan_id=prestamo.id,
                amount=monto,
                # BR-LOAN-017: aparte de `amount` a propósito -- `amount` es
                # lo que se imputa al cronograma, y sumarle el recargo haría
                # que el préstamo se diera por pagado antes de tiempo.
                late_fee_amount=mora_cobrada,
                # BR-LOAN-018: NULL (no 0,00) fuera de una cancelación, para
                # que "sin descuento" y "cobro normal" no se confundan.
                discount_amount=descuento if pago_total else None,
                transfer_reference=referencia_transferencia or None,
                payment_method=medio_pago,
                paid_at=ahora,
                # BR-LOAN-016: el responsable del cobro se guarda en la fila,
                # no solo en el AuditLog, para que el historial pueda decir
                # quién cobró y el comprobante reimpreso nombre al mismo
                # cajero que el original. id_actor_actual devuelve None sin
                # credenciales (tests directos al servicer), igual que en el
                # resto del módulo.
                recorded_by_user_id=id_actor_actual(get_current_claims()),
            )
            sesion.add(pago)
            # El id del pago se necesita en la respuesta (y para el movimiento
            # de caja de más abajo), así que se fuerza antes del commit.
            sesion.flush()

            if sesion_caja is not None:
                # El id ya existe por el flush de arriba; el movimiento de
                # caja lo referencia (BR-CAJA-004).
                registrar_cobro_en_efectivo(
                    sesion,
                    sesion_caja,
                    pago,
                    uuid.UUID(credenciales_efectivo.user_id),
                    ahora,
                )

            sesion.add(
                AuditLog(
                    user_id=id_actor_actual(get_current_claims()),
                    action=(
                        f"PRESTAMO_PAGO_REGISTRADO loan_id={prestamo.id} "
                        f"amount={monto} mora={mora_cobrada} "
                        f"medio={medio_pago.value} "
                        f"referencia={referencia_transferencia}"
                        + (f" cuota={numero_cuota}" if usa_cuota_fija else "")
                        + (f" pago_total=1 descuento={descuento}" if pago_total else "")
                        + (f" caja={sesion_caja.id}" if sesion_caja is not None else "")
                    ),
                    ip_address=ip_remota(context),
                    timestamp=ahora,
                )
            )

            if nuevo_total_pagado >= total_programado:
                prestamo.status = LoanStatusEnum.PAID
                sesion.add(
                    AuditLog(
                        user_id=id_actor_actual(get_current_claims()),
                        action=f"PRESTAMO_PAGADO loan_id={prestamo.id}",
                        ip_address=ip_remota(context),
                        timestamp=ahora,
                    )
                )

            # BR-LOAN-011: se resuelve ANTES del commit porque necesita el
            # acumulado previo a este pago (total_pagado), no el nuevo.
            cuotas_cubiertas = _cuotas_cubiertas_por_pago(prestamo, total_pagado, monto)
            operador = _operador_actual(sesion)

            sesion.commit()

            saldo_restante = max(total_programado - nuevo_total_pagado, CERO)
            nombre_operador, ci_operador = operador
            return loan_service_pb2.RecordPaymentResponse(
                success=True,
                status=prestamo.status.value,
                total_paid=str(nuevo_total_pagado),
                remaining_balance=str(saldo_restante),
                covered_installments=cuotas_cubiertas,
                total_installments=prestamo.term_months,
                amount_paid=str(monto),
                paid_at=a_marca_tiempo(ahora),
                transfer_reference=referencia_transferencia,
                recorded_by_name=nombre_operador,
                recorded_by_national_id=ci_operador,
                payment_method=medio_pago.value,
                payment_id=str(pago.id),
                late_fee_amount=str(mora_cobrada),
                total_charged=str(monto - descuento + mora_cobrada),
                discount_amount=str(descuento),
                paid_in_full=pago_total,
            )

    def ListLoanPayments(self, request, context):
        """BR-LOAN-016: historial de cobros de un préstamo.

        Devuelve por cada pago las mismas cifras que devolvió RecordPayment
        cuando se registró -- imputación FIFO, acumulado y saldo *después* de
        ese pago -- para que un comprobante reimpreso diga lo mismo que el
        original. Se reconstruyen en vez de guardarse por la misma razón por
        la que el cronograma no se persiste: son función del cronograma y de
        los pagos, y duplicarlas abriría la puerta a que la copia y el cálculo
        se contradigan.

        La salvedad honesta es que el cronograma se recalcula con los datos
        actuales del préstamo: si después del cobro se ajustó una cuota
        (BR-LOAN-008) la imputación reconstruida puede no coincidir con la del
        papel original. Por eso la reimpresión se marca como tal en el
        documento, en lugar de hacerse pasar por el primer comprobante.
        """
        loan_id = analizar_uuid(request.loan_id, "loan_id", context)

        with SessionLocal() as sesion:
            prestamo = sesion.get(Loan, loan_id)
            if prestamo is None:
                context.abort(grpc.StatusCode.NOT_FOUND, "Préstamo no encontrado")

            total_programado, total_pagado = _totales_prestamo(prestamo)

            # En orden cronológico para poder acumular; se invierte al final,
            # porque lo que se consulta en ventanilla es el último cobro.
            pagos = sorted(prestamo.payments, key=lambda pago: pago.paid_at)
            acumulado = CERO
            entradas = []
            for pago in pagos:
                cubiertas = _cuotas_cubiertas_por_pago(prestamo, acumulado, pago.amount)
                acumulado += pago.amount
                nombre_operador, ci_operador = _datos_de_operador(pago.recorded_by)
                entradas.append(
                    loan_service_pb2.LoanPaymentEntry(
                        id=str(pago.id),
                        amount=str(pago.amount),
                        paid_at=a_marca_tiempo(pago.paid_at),
                        # NULL se lee como TRANSFERENCIA: era el único medio
                        # admitido antes de BR-CAJA-004 (ver el modelo).
                        payment_method=(
                            pago.payment_method.value
                            if pago.payment_method is not None
                            else PaymentMethodEnum.TRANSFERENCIA.value
                        ),
                        transfer_reference=pago.transfer_reference or "",
                        covered_installments=cubiertas,
                        total_paid_after=str(acumulado),
                        remaining_balance_after=str(
                            max(total_programado - acumulado, CERO)
                        ),
                        recorded_by_name=nombre_operador,
                        recorded_by_national_id=ci_operador,
                        # BR-LOAN-017: la única cifra del historial que se lee
                        # de la fila en vez de reconstruirse. Recalcularla hoy
                        # daría cero -- esa cuota ya está cubierta y no
                        # devenga más -- y el comprobante reimpreso diría que
                        # no se cobró mora cuando sí se cobró. "" en los
                        # cobros anteriores a la regla.
                        late_fee_amount=(
                            ""
                            if pago.late_fee_amount is None
                            else str(pago.late_fee_amount)
                        ),
                        total_charged=str(
                            pago.amount
                            - (pago.discount_amount or CERO)
                            + (pago.late_fee_amount or CERO)
                        ),
                        # BR-LOAN-018: leído de la fila, igual que la mora.
                        discount_amount=(
                            ""
                            if pago.discount_amount is None
                            else str(pago.discount_amount)
                        ),
                    )
                )
            entradas.reverse()

            return loan_service_pb2.ListLoanPaymentsResponse(
                payments=entradas,
                total_installments=prestamo.term_months,
                total_paid=str(total_pagado),
                remaining_balance=str(max(total_programado - total_pagado, CERO)),
            )

    def MarkDefaulted(self, request, context):
        loan_id = analizar_uuid(request.loan_id, "loan_id", context)
        ahora = datetime.now(timezone.utc)

        with SessionLocal() as sesion:
            prestamo = sesion.get(Loan, loan_id)
            if prestamo is None:
                context.abort(grpc.StatusCode.NOT_FOUND, "Préstamo no encontrado")

            if prestamo.status != LoanStatusEnum.ACTIVE:
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "Solo los préstamos en estado ACTIVE pueden marcarse "
                    "como incumplidos (DEFAULTED)",
                )

            prestamo.status = LoanStatusEnum.DEFAULTED

            sesion.add(
                AuditLog(
                    user_id=id_actor_actual(get_current_claims()),
                    action=f"PRESTAMO_INCUMPLIDO loan_id={prestamo.id}",
                    ip_address=ip_remota(context),
                    timestamp=ahora,
                )
            )
            sesion.commit()

            return loan_service_pb2.MarkDefaultedResponse(
                success=True, status=prestamo.status.value
            )

    def RevertDefault(self, request, context):
        """BR-LOAN-014: levanta el incumplimiento y devuelve el préstamo a ACTIVE.

        Sin esta operación un préstamo marcado incumplido queda sin salida:
        `RecordPayment` exige ACTIVE, así que no se le puede cobrar ni cuando el
        cliente regulariza; `DeleteLoan` excluye los estados que ya movieron
        dinero, así que tampoco se borra; y mientras exista con un estado
        distinto de PAID bloquea la baja del cliente (BR-CLI-004).

        El `reason` es obligatorio como en `DeleteLoan`, aunque acá la fila no
        desaparece: revertir un incumplimiento es deshacer el juicio de otro
        operador sobre la cobrabilidad, y el AuditLog es lo único que explica
        por qué se hizo (regularizó, se marcó por error, acuerdo de pago).

        Se toma `with_for_update` sobre la fila, igual que `ApproveLoan`, para
        que dos reversiones simultáneas no escriban ambas su entrada de
        auditoría sobre el mismo cambio de estado.
        """
        loan_id = analizar_uuid(request.loan_id, "loan_id", context)
        motivo = request.reason.strip()
        if not motivo:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "Debe indicarse el motivo por el que se revierte el incumplimiento",
            )
        ahora = datetime.now(timezone.utc)

        with SessionLocal() as sesion:
            prestamo = sesion.get(Loan, loan_id, with_for_update=True)
            if prestamo is None:
                context.abort(grpc.StatusCode.NOT_FOUND, "Préstamo no encontrado")

            if prestamo.status != LoanStatusEnum.DEFAULTED:
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "Solo puede revertirse el incumplimiento de un préstamo "
                    "en estado DEFAULTED",
                )

            # Vuelve a ACTIVE y no a otro estado porque MarkDefaulted solo
            # acepta préstamos ACTIVE: ese es, por construcción, el estado del
            # que salió. Que esté al día o siga en mora lo responde
            # `payment_status` (BR-LOAN-009) recalculado sobre el cronograma,
            # no el estado del préstamo.
            prestamo.status = LoanStatusEnum.ACTIVE

            sesion.add(
                AuditLog(
                    user_id=id_actor_actual(get_current_claims()),
                    action=(
                        f"PRESTAMO_INCUMPLIMIENTO_REVERTIDO loan_id={prestamo.id} "
                        f"motivo={motivo}"
                    ),
                    ip_address=ip_remota(context),
                    timestamp=ahora,
                )
            )
            sesion.commit()

            return loan_service_pb2.RevertDefaultResponse(
                success=True, status=prestamo.status.value
            )

    def GetAmortizationSchedule(self, request, context):
        loan_id = analizar_uuid(request.loan_id, "loan_id", context)

        with SessionLocal() as sesion:
            prestamo = sesion.get(Loan, loan_id)
            if prestamo is None:
                context.abort(grpc.StatusCode.NOT_FOUND, "Préstamo no encontrado")

            filas_con_pendientes = _cronograma_con_pendientes(prestamo)
            total_programado, total_pagado = _totales_prestamo(prestamo)
            saldo_restante = max(total_programado - total_pagado, CERO)
            # BR-LOAN-017: el cronograma en pantalla es donde el operador ve
            # por qué una cuota se cobra más cara que las otras.
            moras = _mora_prestamo(prestamo, datetime.now(timezone.utc).date())

            cuotas = [
                loan_service_pb2.AmortizationInstallment(
                    installment_number=fila.numero,
                    month_offset=fila.numero,
                    payment_amount=str(fila.monto_cuota),
                    principal_portion=str(fila.capital),
                    interest_portion=str(fila.interes),
                    remaining_balance=str(fila.saldo),
                    due_date=fila.fecha_vencimiento.isoformat(),
                    is_adjusted=fila.ajustada,
                    amount_due=str(pendiente),
                    is_paid=pagada,
                    late_fee=str(
                        moras[fila.numero].monto if fila.numero in moras else CERO
                    ),
                    late_fee_days=(
                        moras[fila.numero].dias_punibles if fila.numero in moras else 0
                    ),
                )
                for fila, pendiente, pagada in filas_con_pendientes
            ]

            return loan_service_pb2.GetAmortizationScheduleResponse(
                installments=cuotas,
                total_paid=str(total_pagado),
                remaining_balance=str(saldo_restante),
            )

    def UpdateInstallmentAmount(self, request, context):
        loan_id = analizar_uuid(request.loan_id, "loan_id", context)
        if not request.adjusted_amount:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT, "adjusted_amount es obligatorio"
            )
        monto_ajustado = analizar_decimal(
            request.adjusted_amount, "adjusted_amount", context, permitir_cero=False
        )

        with SessionLocal() as sesion:
            # Se bloquea la fila del préstamo (FOR UPDATE) para serializar
            # ajustes concurrentes a distintas cuotas del mismo préstamo: sin
            # esto, dos llamadas simultáneas validan cada una su cronograma
            # tentativo contra los ajustes ya confirmados, sin ver el ajuste
            # del otro, y el efecto combinado de ambos nunca se valida. Mismo
            # patrón que RecordPayment ya resuelve con with_for_update. Ver
            # ES-006 §3.1.
            prestamo = sesion.get(Loan, loan_id, with_for_update=True)
            if prestamo is None:
                context.abort(grpc.StatusCode.NOT_FOUND, "Préstamo no encontrado")

            if prestamo.status != LoanStatusEnum.ACTIVE:
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "Solo se pueden ajustar cuotas de un préstamo ACTIVE "
                    f"(estado actual: {prestamo.status.value})",
                )

            numero = request.installment_number
            if numero < 1 or numero >= prestamo.term_months:
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    "installment_number debe estar entre 1 y term_months - 1 "
                    "(la última cuota no es ajustable)",
                )

            ajustes_tentativos = dict(_ajustes_prestamo(prestamo))
            ajustes_tentativos[numero] = monto_ajustado
            cronograma_tentativo = calcular_cronograma(
                _monto_financiado(prestamo),
                prestamo.interest_rate,
                prestamo.term_months,
                ajustes=ajustes_tentativos,
            )
            fila_ajustada = next(
                fila for fila in cronograma_tentativo if fila.numero == numero
            )
            if fila_ajustada.capital <= CERO:
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    "El monto ajustado debe ser mayor al interés correspondiente "
                    "a esa cuota",
                )
            if any(fila.saldo < CERO for fila in cronograma_tentativo):
                context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    "El monto ajustado es demasiado alto y dejaría saldo "
                    "negativo en cuotas posteriores; ingrese un monto menor",
                )

            ajuste_existente = (
                sesion.query(LoanInstallmentAdjustment)
                .filter(
                    LoanInstallmentAdjustment.loan_id == prestamo.id,
                    LoanInstallmentAdjustment.installment_number == numero,
                )
                .one_or_none()
            )
            monto_anterior = (
                ajuste_existente.adjusted_amount if ajuste_existente else None
            )
            if ajuste_existente is not None:
                ajuste_existente.adjusted_amount = monto_ajustado
            else:
                sesion.add(
                    LoanInstallmentAdjustment(
                        loan_id=prestamo.id,
                        installment_number=numero,
                        adjusted_amount=monto_ajustado,
                    )
                )

            sesion.add(
                AuditLog(
                    user_id=id_actor_actual(get_current_claims()),
                    action=(
                        f"PRESTAMO_CUOTA_AJUSTADA loan_id={prestamo.id} "
                        f"numero={numero} monto_anterior={monto_anterior} "
                        f"monto_nuevo={monto_ajustado}"
                    ),
                    ip_address=ip_remota(context),
                    timestamp=datetime.now(timezone.utc),
                )
            )
            sesion.commit()

            return loan_service_pb2.UpdateInstallmentAmountResponse(
                success=True, status=prestamo.status.value
            )

    def RemoveInstallmentAdjustment(self, request, context):
        """BR-LOAN-015: quita un ajuste manual y devuelve la cuota al monto
        calculado por el cronograma.

        Sin esta operación un ajuste no tiene vuelta atrás. Volver a tipear el
        monto original **no** alcanza: el reparto del saldo restante se
        recalcula sobre los períodos que faltan, así que los centavos se
        corren a las cuotas siguientes y el cronograma no vuelve a ser el que
        era; además la fila queda marcada "ajustada" para siempre y la
        auditoría registra un segundo ajuste que nunca ocurrió. Es el mismo
        callejón sin salida que BR-LOAN-014 resolvió para el incumplimiento.

        Exige `ACTIVE` como `UpdateInstallmentAmount`: es el único estado en el
        que el cronograma todavía se está cobrando, y no se reescribe el
        reparto de un préstamo ya cancelado o incumplido.

        El `reason` es obligatorio aunque el préstamo no cambie de estado: se
        deshace la decisión de otro operador sobre cuánto debía pagar el
        cliente ese mes, y la fila del ajuste se borra -- el AuditLog, que
        guarda el monto que tenía, es lo único que queda para explicarlo.

        Se toma `with_for_update` sobre el préstamo por la misma razón que
        `UpdateInstallmentAmount`: quitar un ajuste recalcula el reparto de
        todas las cuotas posteriores, así que no puede correr en paralelo con
        otro ajuste del mismo préstamo.
        """
        loan_id = analizar_uuid(request.loan_id, "loan_id", context)
        motivo = request.reason.strip()
        if not motivo:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "Debe indicarse el motivo por el que se quita el ajuste",
            )

        with SessionLocal() as sesion:
            prestamo = sesion.get(Loan, loan_id, with_for_update=True)
            if prestamo is None:
                context.abort(grpc.StatusCode.NOT_FOUND, "Préstamo no encontrado")

            if prestamo.status != LoanStatusEnum.ACTIVE:
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "Solo se pueden ajustar cuotas de un préstamo ACTIVE "
                    f"(estado actual: {prestamo.status.value})",
                )

            numero = request.installment_number
            ajuste = (
                sesion.query(LoanInstallmentAdjustment)
                .filter(
                    LoanInstallmentAdjustment.loan_id == prestamo.id,
                    LoanInstallmentAdjustment.installment_number == numero,
                )
                .one_or_none()
            )
            if ajuste is None:
                context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    f"La cuota {numero} no tiene un ajuste registrado",
                )

            # Quitar un ajuste puede dejar saldo negativo cuando quedan otros:
            # si el ajuste que se quita era chico, esa cuota vuelve a amortizar
            # la porción normal (mayor), el saldo baja más rápido y un ajuste
            # posterior más grande puede pasarse del saldo que queda. Se valida
            # el cronograma resultante igual que en UpdateInstallmentAmount.
            ajustes_tentativos = dict(_ajustes_prestamo(prestamo))
            ajustes_tentativos.pop(numero, None)
            cronograma_tentativo = calcular_cronograma(
                _monto_financiado(prestamo),
                prestamo.interest_rate,
                prestamo.term_months,
                ajustes=ajustes_tentativos,
            )
            if any(fila.saldo < CERO for fila in cronograma_tentativo):
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "Quitar este ajuste dejaría saldo negativo en cuotas "
                    "posteriores; revise primero los otros ajustes del préstamo",
                )

            monto_anterior = ajuste.adjusted_amount
            sesion.delete(ajuste)

            sesion.add(
                AuditLog(
                    user_id=id_actor_actual(get_current_claims()),
                    action=(
                        f"PRESTAMO_AJUSTE_CUOTA_QUITADO loan_id={prestamo.id} "
                        f"numero={numero} monto_anterior={monto_anterior} "
                        f"motivo={motivo}"
                    ),
                    ip_address=ip_remota(context),
                    timestamp=datetime.now(timezone.utc),
                )
            )
            sesion.commit()

            return loan_service_pb2.RemoveInstallmentAdjustmentResponse(
                success=True, status=prestamo.status.value
            )

    def DeleteLoan(self, request, context):
        """BR-LOAN-012: elimina definitivamente un préstamo cargado por error.

        Es la única operación de este servicer que borra una fila en vez de
        cambiarle el estado, así que está acotada por los dos lados: por rol
        (MANAGER_AND_ABOVE en rbac.py) y por estado -- solo PENDING, APPROVED
        o EXPIRED, y únicamente si no tiene ningún pago registrado. Un
        préstamo que ya cobró plata no se borra: sus LoanPayment pueden estar
        imputados a un movimiento de caja de un arqueo ya cerrado, y hacerlos
        desaparecer cambiaría un arqueo firmado (ver BR-CAJA-003).

        La comprobación de pagos es redundante con la de estado (solo un
        préstamo ACTIVE puede recibir pagos), y es a propósito: es la que
        expresa el invariante real -- si mañana se admitiera borrar algún
        estado más, el dinero sigue siendo la línea que no se cruza.
        """
        loan_id = analizar_uuid(request.loan_id, "loan_id", context)
        motivo = request.reason.strip()
        if not motivo:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "reason es obligatorio: el préstamo se borra y el registro de "
                "auditoría es lo único que queda para explicar por qué",
            )
        ahora = datetime.now(timezone.utc)

        with SessionLocal() as sesion:
            # Mismo bloqueo que ApproveLoan/RecordPayment y por el mismo
            # motivo: sin él, un pago concurrente podría entrar entre la
            # verificación de "no tiene pagos" y el DELETE.
            prestamo = sesion.get(Loan, loan_id, with_for_update=True)
            if prestamo is None:
                context.abort(grpc.StatusCode.NOT_FOUND, "Préstamo no encontrado")

            if prestamo.status not in _ESTADOS_ELIMINABLES:
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "Solo se puede eliminar un préstamo que todavía no fue "
                    "desembolsado (Pendiente, Aprobado o Rechazado); este está "
                    f"en estado {prestamo.status.value} (BR-LOAN-012)",
                )

            if prestamo.payments:
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "El préstamo tiene pagos registrados y no puede eliminarse "
                    "(BR-LOAN-012)",
                )

            # Datos del préstamo antes de borrarlo: una vez hecho el DELETE la
            # fila no existe, así que el AuditLog tiene que ser autosuficiente
            # para reconstruir qué se eliminó.
            client_id = prestamo.client_id
            estado_previo = prestamo.status.value
            resumen = (
                f"capital={prestamo.principal_amount} "
                f"tasa={prestamo.interest_rate} cuotas={prestamo.term_months} "
                f"primer_vencimiento={prestamo.first_due_date.isoformat()}"
            )

            # Los ajustes de cuota solo existen para préstamos ACTIVE, que no
            # son eliminables -- se borran igual para que la FK no dependa de
            # esa coincidencia si algún día cambia.
            sesion.query(LoanInstallmentAdjustment).filter(
                LoanInstallmentAdjustment.loan_id == prestamo.id
            ).delete(synchronize_session=False)
            sesion.delete(prestamo)

            sesion.add(
                AuditLog(
                    user_id=id_actor_actual(get_current_claims()),
                    action=(
                        f"PRESTAMO_ELIMINADO loan_id={loan_id} "
                        f"client_id={client_id} estado={estado_previo} "
                        f"{resumen} motivo={motivo}"
                    ),
                    ip_address=ip_remota(context),
                    timestamp=ahora,
                )
            )
            sesion.commit()

            return loan_service_pb2.DeleteLoanResponse(
                success=True,
                client_id=str(client_id),
                deleted_status=estado_previo,
            )
