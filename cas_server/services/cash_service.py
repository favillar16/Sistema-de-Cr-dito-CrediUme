"""Módulo de caja: apertura de turno, movimientos de efectivo y arqueo de
cierre (BR-CAJA-001..004).

Sigue la convención en español de client_service.py / loan_service.py:
variables, docstrings, mensajes de `context.abort()` y acciones de AuditLog en
español; los símbolos generados por protobuf y los nombres de campo del
contrato quedan en inglés.

Dos ideas atraviesan todo el módulo:

1. **El turno se deduce del token, no del request.** Ninguna RPC recibe el id
   del cajero, y solo CloseCashSession acepta un `session_id` explícito (para
   que un Gerente cierre una caja que quedó abierta). Así un cajero no puede
   cargar movimientos ni cerrar la caja de otro.

2. **El "esperado" del arqueo lo calcula el servidor.** El cliente solo
   declara cuánto contó; el monto contra el que se compara sale siempre de
   `opening_amount` más los movimientos del turno. Un arqueo donde el cliente
   mandara ambos números no probaría nada.
"""

import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import grpc

import cash_service_pb2
import cash_service_pb2_grpc
from cas_server.db.base import SessionLocal
from cas_server.db.models import (
    AuditLog,
    CashMovement,
    CashMovementTypeEnum,
    CashSession,
    CashSessionStatusEnum,
    LoanPayment,
    RoleEnum,
    User,
)
from cas_server.security import rbac
from cas_server.security.current_user import get_current_claims
from cas_server.services.common import (
    analizar_decimal,
    analizar_fecha,
    analizar_uuid,
    a_marca_tiempo,
    confirmar_o_duplicado,
    ip_remota,
)

CERO = Decimal("0.00")

# Concepto con el que se registra el movimiento automático de un cobro de
# cuota en efectivo (BR-CAJA-004). Se arma acá y no en loan_service.py para
# que todos los movimientos automáticos se lean igual en el arqueo.
_CONCEPTO_COBRO = "Cobro de cuota en efectivo"


def _dos_decimales(valor: Decimal) -> Decimal:
    """Los montos viajan como string y se comparan de a pares en el arqueo --
    normalizar la escala evita que "0" y "0.00" se lean como distintos."""
    return valor.quantize(Decimal("0.01"))


def _credenciales_o_abortar(context: grpc.ServicerContext):
    """A diferencia de loan_service.py (donde `claims is None` se tolera
    porque el actor solo alimenta el AuditLog), acá la identidad ES el dato:
    no existe "la caja" sin un cajero. Un llamado sin credenciales es un error
    de programación, no un caso a ignorar en silencio."""
    credenciales = get_current_claims()
    if credenciales is None:
        context.abort(
            grpc.StatusCode.UNAUTHENTICATED,
            "La operación de caja requiere un usuario autenticado",
        )
    return credenciales


def _es_supervisor(credenciales) -> bool:
    """Gerente o Administrador -- ven los arqueos de todos los cajeros y
    pueden cerrar una caja ajena (BR-CAJA-003)."""
    try:
        return RoleEnum(credenciales.role) in rbac.MANAGER_AND_ABOVE
    except ValueError:  # rol desconocido en el token -- se trata como no supervisor
        return False


def _nombre_completo(usuario: User | None) -> str:
    if usuario is None or not usuario.first_name or not usuario.last_name:
        return ""
    return f"{usuario.first_name} {usuario.last_name}"


def obtener_sesion_abierta(
    sesion, user_id: uuid.UUID, *, bloquear: bool = False
) -> CashSession | None:
    """Turno abierto del usuario, o None. Con `bloquear=True` toma un FOR
    UPDATE sobre la fila para serializar contra otro movimiento o contra el
    cierre -- sin eso, un movimiento concurrente podría entrar entre el
    cálculo del esperado y el cierre, y quedar fuera del arqueo que ya se
    firmó. Lo usa también loan_service.py al cobrar una cuota en efectivo.
    """
    consulta = sesion.query(CashSession).filter(
        CashSession.user_id == user_id,
        CashSession.status == CashSessionStatusEnum.OPEN,
    )
    if bloquear:
        consulta = consulta.with_for_update()
    return consulta.one_or_none()


def registrar_cobro_en_efectivo(
    sesion,
    sesion_caja: CashSession,
    pago: LoanPayment,
    user_id: uuid.UUID | None,
    momento: datetime,
) -> CashMovement:
    """Movimiento de INGRESO que deja un cobro de cuota en efectivo imputado
    a la caja (BR-CAJA-004). Vive acá y no en loan_service.py para que la
    forma del movimiento (tipo, concepto, vínculo con el pago) esté en un
    solo lugar junto al resto del arqueo."""
    movimiento = CashMovement(
        cash_session_id=sesion_caja.id,
        movement_type=CashMovementTypeEnum.INGRESO,
        amount=pago.amount,
        concept=_CONCEPTO_COBRO,
        loan_payment_id=pago.id,
        created_by_user_id=user_id,
        created_at=momento,
    )
    sesion.add(movimiento)
    return movimiento


def _totales_sesion(
    sesion_caja: CashSession,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """(ingresos, egresos, cobros de préstamo, esperado en caja).

    Los cobros de préstamo son un subconjunto de los ingresos, no un tercer
    término: ya están sumados dentro de `ingresos`.
    """
    ingresos = CERO
    egresos = CERO
    cobros = CERO
    for movimiento in sesion_caja.movements:
        if movimiento.movement_type == CashMovementTypeEnum.INGRESO:
            ingresos += movimiento.amount
            if movimiento.loan_payment_id is not None:
                cobros += movimiento.amount
        else:
            egresos += movimiento.amount
    esperado = sesion_caja.opening_amount + ingresos - egresos
    return (
        _dos_decimales(ingresos),
        _dos_decimales(egresos),
        _dos_decimales(cobros),
        _dos_decimales(esperado),
    )


def _movimiento_a_entrada(
    movimiento: CashMovement,
) -> cash_service_pb2.CashMovementEntry:
    return cash_service_pb2.CashMovementEntry(
        id=str(movimiento.id),
        movement_type=movimiento.movement_type.value,
        amount=str(movimiento.amount),
        concept=movimiento.concept,
        created_at=a_marca_tiempo(movimiento.created_at),
        loan_payment_id=(
            ""
            if movimiento.loan_payment_id is None
            else str(movimiento.loan_payment_id)
        ),
        is_automatic=movimiento.loan_payment_id is not None,
    )


def _campos_arqueo(sesion_caja: CashSession, sesion) -> dict:
    """Los tres números del arqueo más quién lo firmó -- vacíos mientras el
    turno siga abierto. Compartido por el detalle y el resumen del historial
    para que ambos no puedan discrepar."""
    if sesion_caja.status != CashSessionStatusEnum.CLOSED:
        return {}
    cerrado_por = (
        sesion.get(User, sesion_caja.closed_by_user_id)
        if sesion_caja.closed_by_user_id is not None
        else None
    )
    campos = {
        "closing_counted_amount": str(sesion_caja.closing_counted_amount),
        "closing_difference": str(sesion_caja.closing_difference),
        "closing_notes": sesion_caja.closing_notes or "",
        "closed_by_username": "" if cerrado_por is None else cerrado_por.username,
    }
    if sesion_caja.closed_at is not None:
        campos["closed_at"] = a_marca_tiempo(sesion_caja.closed_at)
    return campos


def _detalle_sesion(
    sesion, sesion_caja: CashSession
) -> cash_service_pb2.CashSessionDetail:
    ingresos, egresos, cobros, esperado = _totales_sesion(sesion_caja)
    cajero = sesion.get(User, sesion_caja.user_id)
    movimientos = sorted(sesion_caja.movements, key=lambda m: m.created_at)

    argumentos = dict(
        id=str(sesion_caja.id),
        cashier_username="" if cajero is None else cajero.username,
        cashier_full_name=_nombre_completo(cajero),
        status=sesion_caja.status.value,
        opening_amount=str(sesion_caja.opening_amount),
        opened_at=a_marca_tiempo(sesion_caja.opened_at),
        opening_notes=sesion_caja.opening_notes or "",
        total_income=str(ingresos),
        total_expense=str(egresos),
        total_loan_collections=str(cobros),
        expected_amount=str(esperado),
        movements_count=len(movimientos),
        movements=[_movimiento_a_entrada(m) for m in movimientos],
    )
    arqueo = _campos_arqueo(sesion_caja, sesion)
    if arqueo:
        # `closing_expected_amount` sale de la columna persistida, no del
        # recálculo de arriba: un arqueo cerrado es un registro histórico y
        # tiene que seguir mostrando el número contra el que se comparó.
        arqueo["closing_expected_amount"] = str(sesion_caja.closing_expected_amount)
        argumentos.update(arqueo)
    return cash_service_pb2.CashSessionDetail(**argumentos)


def _resumen_sesion(
    sesion, sesion_caja: CashSession
) -> cash_service_pb2.CashSessionSummary:
    ingresos, egresos, cobros, esperado = _totales_sesion(sesion_caja)
    cajero = sesion.get(User, sesion_caja.user_id)

    argumentos = dict(
        id=str(sesion_caja.id),
        cashier_username="" if cajero is None else cajero.username,
        cashier_full_name=_nombre_completo(cajero),
        status=sesion_caja.status.value,
        opening_amount=str(sesion_caja.opening_amount),
        opened_at=a_marca_tiempo(sesion_caja.opened_at),
        total_income=str(ingresos),
        total_expense=str(egresos),
        total_loan_collections=str(cobros),
        expected_amount=str(esperado),
        movements_count=len(sesion_caja.movements),
    )
    argumentos.update(_campos_arqueo(sesion_caja, sesion))
    return cash_service_pb2.CashSessionSummary(**argumentos)


def _inicio_del_dia(dia: date) -> datetime:
    """Mismo criterio que dashboard_service.py: los campos de fecha/hora son
    DateTime(timezone=True) en UTC y el filtro llega como fechas sueltas."""
    return datetime.combine(dia, time.min, tzinfo=timezone.utc)


class CashServicer(cash_service_pb2_grpc.CashServiceServicer):
    def OpenCashSession(self, request, context):
        credenciales = _credenciales_o_abortar(context)
        monto_apertura = _dos_decimales(
            analizar_decimal(request.opening_amount, "opening_amount", context)
        )
        ahora = datetime.now(timezone.utc)
        user_id = uuid.UUID(credenciales.user_id)

        with SessionLocal() as sesion:
            sesion_caja = CashSession(
                user_id=user_id,
                status=CashSessionStatusEnum.OPEN,
                opening_amount=monto_apertura,
                opened_at=ahora,
                opening_notes=request.notes.strip() or None,
            )
            sesion.add(sesion_caja)
            # BR-CAJA-002: el índice único parcial sobre las filas OPEN es lo
            # que impide dos turnos abiertos del mismo cajero. Se confía en él
            # en vez de en un SELECT previo porque dos aperturas concurrentes
            # no se verían entre sí (ES-006 §3.1); el flush explícito hace que
            # el conflicto salte acá y no en el commit posterior.
            confirmar_o_duplicado(
                sesion,
                context,
                "Ya existe una caja abierta para este usuario",
                accion=sesion.flush,
            )

            sesion.add(
                AuditLog(
                    user_id=user_id,
                    action=(
                        f"CAJA_ABIERTA session_id={sesion_caja.id} "
                        f"monto_apertura={monto_apertura}"
                    ),
                    ip_address=ip_remota(context),
                    timestamp=ahora,
                )
            )
            sesion.commit()
            sesion.refresh(sesion_caja)
            return _detalle_sesion(sesion, sesion_caja)

    def GetCurrentCashSession(self, request, context):
        credenciales = _credenciales_o_abortar(context)

        with SessionLocal() as sesion:
            sesion_caja = obtener_sesion_abierta(
                sesion, uuid.UUID(credenciales.user_id)
            )
            if sesion_caja is None:
                # No tener caja abierta es un estado normal (el cajero todavía
                # no llegó, o ya cerró), no un NOT_FOUND -- la pantalla de caja
                # necesita distinguirlo para ofrecer "Abrir caja".
                return cash_service_pb2.GetCurrentCashSessionResponse(
                    has_open_session=False
                )
            return cash_service_pb2.GetCurrentCashSessionResponse(
                has_open_session=True,
                session=_detalle_sesion(sesion, sesion_caja),
            )

    def RegisterCashMovement(self, request, context):
        credenciales = _credenciales_o_abortar(context)
        tipo_texto = request.movement_type.strip().upper()
        try:
            tipo = CashMovementTypeEnum(tipo_texto)
        except ValueError:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "movement_type debe ser INGRESO o EGRESO",
            )
        monto = _dos_decimales(
            analizar_decimal(request.amount, "amount", context, permitir_cero=False)
        )
        concepto = request.concept.strip()
        if not concepto:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "concept es obligatorio")

        ahora = datetime.now(timezone.utc)
        user_id = uuid.UUID(credenciales.user_id)

        with SessionLocal() as sesion:
            sesion_caja = obtener_sesion_abierta(sesion, user_id, bloquear=True)
            if sesion_caja is None:
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "No hay una caja abierta para este usuario",
                )

            # Un egreso no puede dejar la caja en negativo: no se puede sacar
            # efectivo que no está. Se valida contra el esperado actual, que
            # es justamente lo que debería haber en el cajón.
            _, _, _, esperado = _totales_sesion(sesion_caja)
            if tipo == CashMovementTypeEnum.EGRESO and monto > esperado:
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    f"El egreso supera el efectivo disponible en caja ({esperado})",
                )

            sesion.add(
                CashMovement(
                    cash_session_id=sesion_caja.id,
                    movement_type=tipo,
                    amount=monto,
                    concept=concepto,
                    created_by_user_id=user_id,
                    created_at=ahora,
                )
            )
            sesion.add(
                AuditLog(
                    user_id=user_id,
                    action=(
                        f"CAJA_MOVIMIENTO session_id={sesion_caja.id} "
                        f"tipo={tipo.value} monto={monto} concepto={concepto}"
                    ),
                    ip_address=ip_remota(context),
                    timestamp=ahora,
                )
            )
            sesion.commit()
            sesion.refresh(sesion_caja)
            return _detalle_sesion(sesion, sesion_caja)

    def CloseCashSession(self, request, context):
        credenciales = _credenciales_o_abortar(context)
        monto_contado = _dos_decimales(
            analizar_decimal(request.counted_amount, "counted_amount", context)
        )
        session_id = (
            analizar_uuid(request.session_id, "session_id", context)
            if request.session_id
            else None
        )
        ahora = datetime.now(timezone.utc)
        user_id = uuid.UUID(credenciales.user_id)

        with SessionLocal() as sesion:
            if session_id is None:
                sesion_caja = obtener_sesion_abierta(sesion, user_id, bloquear=True)
                if sesion_caja is None:
                    context.abort(
                        grpc.StatusCode.FAILED_PRECONDITION,
                        "No hay una caja abierta para este usuario",
                    )
            else:
                sesion_caja = sesion.get(CashSession, session_id, with_for_update=True)
                if sesion_caja is None:
                    context.abort(
                        grpc.StatusCode.NOT_FOUND, "Turno de caja no encontrado"
                    )
                # BR-CAJA-003: cerrar la caja de otro es una intervención de
                # supervisión (el cajero se fue sin cerrar), no algo que un
                # cajero pueda hacerle a un par.
                if sesion_caja.user_id != user_id and not _es_supervisor(credenciales):
                    context.abort(
                        grpc.StatusCode.PERMISSION_DENIED,
                        "Solo un Gerente o Administrador puede cerrar la caja de otro usuario",
                    )
                if sesion_caja.status != CashSessionStatusEnum.OPEN:
                    context.abort(
                        grpc.StatusCode.FAILED_PRECONDITION,
                        "El turno de caja ya está cerrado",
                    )

            _, _, _, esperado = _totales_sesion(sesion_caja)
            diferencia = _dos_decimales(monto_contado - esperado)

            sesion_caja.status = CashSessionStatusEnum.CLOSED
            sesion_caja.closing_expected_amount = esperado
            sesion_caja.closing_counted_amount = monto_contado
            sesion_caja.closing_difference = diferencia
            sesion_caja.closed_at = ahora
            sesion_caja.closing_notes = request.notes.strip() or None
            sesion_caja.closed_by_user_id = user_id

            sesion.add(
                AuditLog(
                    user_id=user_id,
                    action=(
                        f"CAJA_CERRADA session_id={sesion_caja.id} "
                        f"esperado={esperado} contado={monto_contado} "
                        f"diferencia={diferencia}"
                    ),
                    ip_address=ip_remota(context),
                    timestamp=ahora,
                )
            )
            sesion.commit()
            sesion.refresh(sesion_caja)
            return _detalle_sesion(sesion, sesion_caja)

    def ListCashSessions(self, request, context):
        credenciales = _credenciales_o_abortar(context)
        desde = (
            _inicio_del_dia(analizar_fecha(request.start_date, "start_date", context))
            if request.start_date
            else None
        )
        hasta = (
            _inicio_del_dia(
                analizar_fecha(request.end_date, "end_date", context)
                + timedelta(days=1)
            )
            if request.end_date
            else None
        )
        if desde is not None and hasta is not None and hasta <= desde:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "start_date no puede ser posterior a end_date",
            )

        with SessionLocal() as sesion:
            consulta = sesion.query(CashSession)
            # El alcance sale del rol del token, no de un parámetro: un cajero
            # solo ve sus propios arqueos aunque pida otra cosa.
            if not _es_supervisor(credenciales):
                consulta = consulta.filter(
                    CashSession.user_id == uuid.UUID(credenciales.user_id)
                )
            if desde is not None:
                consulta = consulta.filter(CashSession.opened_at >= desde)
            if hasta is not None:
                consulta = consulta.filter(CashSession.opened_at < hasta)

            turnos = consulta.order_by(CashSession.opened_at.desc()).all()
            return cash_service_pb2.ListCashSessionsResponse(
                sessions=[_resumen_sesion(sesion, turno) for turno in turnos]
            )
