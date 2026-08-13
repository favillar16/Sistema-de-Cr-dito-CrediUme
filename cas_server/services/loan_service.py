from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

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
    RoleEnum,
    User,
)
from cas_server.security import rbac
from cas_server.security.current_user import get_current_claims
from cas_server.services.amortization import calcular_cronograma
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
        prestamo.principal_amount,
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
        prestamo.principal_amount,
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


def _total_cargos(prestamo: Loan) -> Decimal:
    """BR-LOAN-006: suma de los 4 cargos/seguros opcionales, puramente informativa."""
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


def _nombre_usuario_creador(sesion, prestamo: Loan) -> str:
    """Username de quien registró el préstamo (CreateLoan), o "" si no se
    conoce -- ver el comentario de Loan.created_by_user_id en models.py."""
    if prestamo.created_by_user_id is None:
        return ""
    creador = sesion.get(User, prestamo.created_by_user_id)
    return creador.username if creador is not None else ""


def _prestamo_a_respuesta(
    prestamo: Loan, sesion
) -> loan_service_pb2.GetLoanByIdResponse:
    total_programado, total_pagado = _totales_prestamo(prestamo)
    saldo_restante = max(total_programado - total_pagado, CERO)
    total_cargos = _total_cargos(prestamo)
    estado_pago, monto_vencido, cuotas_vencidas = _estado_pago_prestamo(
        prestamo, datetime.now(timezone.utc).date()
    )
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
        total_credit_with_charges=str(total_programado + total_cargos),
        payment_status=estado_pago,
        overdue_amount=str(monto_vencido),
        overdue_installments_count=cuotas_vencidas,
        created_by_username=_nombre_usuario_creador(sesion, prestamo),
    )
    if prestamo.approved_at is not None:
        argumentos["approved_at"] = a_marca_tiempo(prestamo.approved_at)
    return loan_service_pb2.GetLoanByIdResponse(**argumentos)


class LoanServicer(loan_service_pb2_grpc.LoanServiceServicer):
    def CreateLoan(self, request, context):
        if not (
            request.client_id and request.principal_amount and request.interest_rate
        ):
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "client_id, principal_amount e interest_rate son obligatorios",
            )
        if request.term_months <= 0:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT, "term_months debe ser positivo"
            )

        client_id = analizar_uuid(request.client_id, "client_id", context)
        capital = analizar_decimal(
            request.principal_amount, "principal_amount", context, permitir_cero=False
        )
        tasa_interes = analizar_decimal(request.interest_rate, "interest_rate", context)

        # BR-LOAN-007: solo Agente de Créditos (MANAGER) o Administrador pueden
        # fijar una tasa distinta a la estándar. `claims` es None únicamente en
        # llamadas directas al servicer sin pasar por AuthInterceptor (tests) --
        # en producción esta RPC siempre tiene claims, así que no restringimos
        # cuando no hay contexto de autenticación disponible.
        claims = get_current_claims()
        if claims is not None:
            rol_actual = RoleEnum(claims.role)
            if (
                rol_actual not in rbac.MANAGER_AND_ABOVE
                and tasa_interes != config.LOAN_FIXED_INTEREST_RATE
            ):
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "Solo un Agente de Créditos o Administrador puede fijar una "
                    "tasa distinta a la estándar "
                    f"({config.LOAN_FIXED_INTEREST_RATE}) (BR-LOAN-007)",
                )

        ahora = datetime.now(timezone.utc)
        fecha_primer_vencimiento = ahora.date() + timedelta(
            days=config.LOAN_DEFAULT_FIRST_DUE_DAYS
        )

        with SessionLocal() as sesion:
            cliente = sesion.get(Client, client_id)
            if cliente is None:
                context.abort(grpc.StatusCode.NOT_FOUND, "Cliente no encontrado")
            if not cliente.is_active:
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION, "El cliente no está activo"
                )

            if cliente.declared_monthly_income is None:
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "El cliente no tiene ingresos declarados registrados (BR-LOAN-002)",
                )

            cronograma = calcular_cronograma(capital, tasa_interes, request.term_months)
            cuota_mensual = cronograma[0].monto_cuota
            cuota_maxima = (
                cliente.declared_monthly_income
                * config.LOAN_MAX_INSTALLMENT_INCOME_RATIO
            )
            if cuota_mensual > cuota_maxima:
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "La cuota mensual excede el 40% del ingreso declarado (BR-LOAN-002)",
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
                status=LoanStatusEnum.PENDING,
                created_at=ahora,
            )
            sesion.add(prestamo)
            sesion.flush()

            sesion.add(
                AuditLog(
                    user_id=id_actor_actual(get_current_claims()),
                    action=f"PRESTAMO_CREADO loan_id={prestamo.id} client_id={cliente.id}",
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

            cliente = sesion.get(Client, prestamo.client_id)
            cronograma = calcular_cronograma(
                capital, prestamo.interest_rate, request.term_months
            )
            cuota_mensual = cronograma[0].monto_cuota
            cuota_maxima = (
                cliente.declared_monthly_income
                * config.LOAN_MAX_INSTALLMENT_INCOME_RATIO
            )
            if cuota_mensual > cuota_maxima:
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "La cuota mensual excede el 40% del ingreso declarado (BR-LOAN-002)",
                )

            monto_anterior = prestamo.principal_amount
            cuotas_anterior = prestamo.term_months
            prestamo.principal_amount = capital
            prestamo.term_months = request.term_months
            prestamo.first_due_date = fecha_primer_vencimiento

            sesion.add(
                AuditLog(
                    user_id=id_actor_actual(get_current_claims()),
                    action=(
                        f"PRESTAMO_PROPUESTA_ACTUALIZADA loan_id={prestamo.id} "
                        f"monto_anterior={monto_anterior} monto_nuevo={capital} "
                        f"cuotas_anterior={cuotas_anterior} "
                        f"cuotas_nuevo={request.term_months}"
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

            prestamo.guarantee_type = tipo or None
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

            def _cargo(valor: str, nombre_campo: str) -> Decimal | None:
                texto = valor.strip()
                return analizar_decimal(texto, nombre_campo, context) if texto else None

            prestamo.charge_interest_tax = _cargo(
                request.charge_interest_tax, "charge_interest_tax"
            )
            prestamo.charge_admin_fee = _cargo(
                request.charge_admin_fee, "charge_admin_fee"
            )
            prestamo.charge_cancellation_insurance = _cargo(
                request.charge_cancellation_insurance, "charge_cancellation_insurance"
            )
            prestamo.charge_contracted_insurance = _cargo(
                request.charge_contracted_insurance, "charge_contracted_insurance"
            )

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
            prestamo = sesion.get(Loan, loan_id)
            if prestamo is None:
                context.abort(grpc.StatusCode.NOT_FOUND, "Préstamo no encontrado")

            if prestamo.status != LoanStatusEnum.PENDING:
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "El préstamo no está en estado PENDING",
                )

            # BR-LOAN-001: un cliente puede acumular préstamos PENDING sin
            # límite (no cuentan para el tope), así que la aprobación debe
            # volver a verificarlo aquí también.
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
        # El cobro en efectivo no se usa (rol CASHIER fuera de uso) -- todo pago
        # se hace por transferencia, descuento directo o descuento en cuenta
        # específica, así que la referencia es obligatoria para trazabilidad.
        referencia_transferencia = request.transfer_reference.strip()
        if not referencia_transferencia:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT, "transfer_reference es obligatorio"
            )

        # BR-LOAN-010: si se especifica una cuota puntual, el monto se
        # recalcula acá mismo a partir del cronograma -- nunca se confía en
        # un `amount` que mande el cliente para ese caso, así el monto
        # registrado siempre es el fijo que corresponde a esa cuota.
        numero_cuota = request.installment_number
        usa_cuota_fija = numero_cuota > 0
        if not usa_cuota_fija and not request.amount:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "amount es obligatorio")
        monto = (
            None
            if usa_cuota_fija
            else analizar_decimal(
                request.amount, "amount", context, permitir_cero=False
            )
        )
        ahora = datetime.now(timezone.utc)

        with SessionLocal() as sesion:
            prestamo = sesion.get(Loan, loan_id)
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
            nuevo_total_pagado = total_pagado + monto

            sesion.add(
                LoanPayment(
                    loan_id=prestamo.id,
                    amount=monto,
                    transfer_reference=referencia_transferencia,
                    paid_at=ahora,
                )
            )

            sesion.add(
                AuditLog(
                    user_id=id_actor_actual(get_current_claims()),
                    action=(
                        f"PRESTAMO_PAGO_REGISTRADO loan_id={prestamo.id} "
                        f"amount={monto} referencia={referencia_transferencia}"
                        + (f" cuota={numero_cuota}" if usa_cuota_fija else "")
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

            sesion.commit()

            saldo_restante = max(total_programado - nuevo_total_pagado, CERO)
            return loan_service_pb2.RecordPaymentResponse(
                success=True,
                status=prestamo.status.value,
                total_paid=str(nuevo_total_pagado),
                remaining_balance=str(saldo_restante),
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

    def GetAmortizationSchedule(self, request, context):
        loan_id = analizar_uuid(request.loan_id, "loan_id", context)

        with SessionLocal() as sesion:
            prestamo = sesion.get(Loan, loan_id)
            if prestamo is None:
                context.abort(grpc.StatusCode.NOT_FOUND, "Préstamo no encontrado")

            filas_con_pendientes = _cronograma_con_pendientes(prestamo)
            total_programado, total_pagado = _totales_prestamo(prestamo)
            saldo_restante = max(total_programado - total_pagado, CERO)

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
            prestamo = sesion.get(Loan, loan_id)
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
                prestamo.principal_amount,
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
