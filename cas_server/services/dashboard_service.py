"""Estadísticas agregadas de solo lectura para el dashboard (pantalla de
inicio del cliente) -- no persiste tablas propias, agrega sobre Client/Loan
ya existentes. Sigue la convención en español de client_service.py y
loan_service.py."""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import grpc

import dashboard_service_pb2
import dashboard_service_pb2_grpc
from cas_server.db.base import SessionLocal
from cas_server.db.models import Client, Loan, LoanPayment, LoanStatusEnum

# Reutiliza el chequeo perezoso de vencimiento (BR-LOAN-003) ya implementado
# en loan_service.py en vez de duplicar esa regla acá -- mismo patrón que
# ListClientLoans/GetLoanById dentro de ese mismo módulo. _estado_pago_prestamo
# (BR-LOAN-009) se reutiliza por la misma razón: el monto de mora por préstamo
# ya se calcula ahí para GetLoanByIdResponse.overdue_amount, y el total del
# dashboard (BR-DASH-001) tiene que dar exactamente lo mismo sumado.
from cas_server.services.common import a_marca_tiempo, analizar_fecha
from cas_server.services.loan_service import (
    _auditar_vencidos,
    _cronograma_con_pendientes,
    _estado_pago_prestamo,
    _totales_prestamo,
    _vencer_atrasados_y_contar_activos,
)

CERO = Decimal("0.00")

_ESTADOS_DESEMBOLSADOS = (
    LoanStatusEnum.ACTIVE,
    LoanStatusEnum.PAID,
    LoanStatusEnum.DEFAULTED,
)


def _inicio_del_dia(dia: date) -> datetime:
    """Los campos de fecha/hora del modelo son DateTime(timezone=True) en UTC;
    el rango del reporte llega como fechas sueltas. Se comparan como
    [00:00 del día inicial, 00:00 del día siguiente al final), de modo que el
    último día quede incluido entero."""
    return datetime.combine(dia, time.min, tzinfo=timezone.utc)


def _en_rango(momento: datetime | None, desde: datetime, hasta: datetime) -> bool:
    if momento is None:
        return False
    # Postgres devuelve estos campos con tzinfo, pero una fila escrita por un
    # camino que guardó un naive datetime rompería la comparación -- se asume
    # UTC en ese caso, igual criterio que el resto del servidor.
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    return desde <= momento < hasta


def _proxima_cuota_impaga(prestamo: Loan) -> tuple[date, Decimal] | None:
    """(fecha de vencimiento, monto todavía pendiente) de la primera cuota que
    sigue sin cubrirse, o None si el cronograma ya está saldado.

    Se apoya en el mismo recorrido FIFO que BR-LOAN-009/BR-LOAN-010
    (_cronograma_con_pendientes) en vez de recalcular la imputación: el
    "próximo vencimiento" que ve el gestor de cobranza tiene que ser el mismo
    que muestra la pantalla del préstamo.
    """
    for fila, pendiente, pagada in _cronograma_con_pendientes(prestamo):
        if not pagada and fila.fecha_vencimiento is not None:
            return fila.fecha_vencimiento, pendiente
    return None


class DashboardServicer(dashboard_service_pb2_grpc.DashboardServiceServicer):
    def GetDashboardStats(self, request, context):
        ahora = datetime.now(timezone.utc)

        with SessionLocal() as sesion:
            total_clientes = sesion.query(Client).count()
            clientes_activos = (
                sesion.query(Client).filter(Client.is_active.is_(True)).count()
            )

            prestamos = sesion.query(Loan).all()
            _, vencidos = _vencer_atrasados_y_contar_activos(prestamos, ahora)
            if vencidos:
                _auditar_vencidos(sesion, vencidos, context, ahora)
                sesion.commit()

            conteos = {estado: 0 for estado in LoanStatusEnum}
            total_desembolsado = CERO
            saldo_pendiente = CERO
            total_mora = CERO
            prestamos_en_mora = 0
            hoy = ahora.date()
            for prestamo in prestamos:
                conteos[prestamo.status] += 1
                if prestamo.status in _ESTADOS_DESEMBOLSADOS:
                    total_desembolsado += prestamo.principal_amount
                if prestamo.status == LoanStatusEnum.ACTIVE:
                    total_programado, total_pagado = _totales_prestamo(prestamo)
                    saldo_pendiente += max(total_programado - total_pagado, CERO)
                    # BR-DASH-001: lo vencido e impago, que es un subconjunto
                    # del saldo pendiente de arriba (ese incluye también las
                    # cuotas futuras todavía no exigibles).
                    _, monto_vencido, _ = _estado_pago_prestamo(prestamo, hoy)
                    total_mora += monto_vencido
                    if monto_vencido > CERO:
                        prestamos_en_mora += 1

            return dashboard_service_pb2.GetDashboardStatsResponse(
                total_clients_count=total_clientes,
                active_clients_count=clientes_activos,
                pending_loans_count=conteos[LoanStatusEnum.PENDING],
                approved_loans_count=conteos[LoanStatusEnum.APPROVED],
                active_loans_count=conteos[LoanStatusEnum.ACTIVE],
                paid_loans_count=conteos[LoanStatusEnum.PAID],
                defaulted_loans_count=conteos[LoanStatusEnum.DEFAULTED],
                expired_loans_count=conteos[LoanStatusEnum.EXPIRED],
                total_disbursed=str(total_desembolsado),
                total_outstanding_balance=str(saldo_pendiente),
                total_overdue_amount=str(total_mora),
                overdue_loans_count=prestamos_en_mora,
            )

    def GetPeriodReport(self, request, context):
        """BR-DASH-002: cierre de período. Mezcla dos cosas distintas a
        propósito, y las nombra distinto en la respuesta para que no se
        confundan: lo que *ocurrió* dentro del rango (altas, aprobaciones,
        desembolsos, cobranza) y la *foto al cierre* (cartera activa, saldo,
        mora), que se mide al momento de generar el reporte y no depende del
        rango.

        Los tres renglones de capital del primer bloque miden cosas distintas
        y no tienen por qué coincidir: `principal_created` es lo pedido,
        `principal_approved` lo autorizado y `principal_disbursed` lo
        efectivamente entregado. Un préstamo puede aparecer en el primero de
        un mes y en el tercero del siguiente, o no llegar nunca al tercero si
        la aprobación caduca (BR-LOAN-003)."""
        fecha_inicio = analizar_fecha(request.start_date, "start_date", context)
        fecha_fin = analizar_fecha(request.end_date, "end_date", context)
        if fecha_fin < fecha_inicio:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "end_date no puede ser anterior a start_date",
            )

        desde = _inicio_del_dia(fecha_inicio)
        hasta = _inicio_del_dia(fecha_fin + timedelta(days=1))
        ahora = datetime.now(timezone.utc)

        with SessionLocal() as sesion:
            clientes_registrados = sum(
                1
                for cliente in sesion.query(Client).all()
                if _en_rango(cliente.created_at, desde, hasta)
            )

            prestamos = sesion.query(Loan).all()
            _, vencidos = _vencer_atrasados_y_contar_activos(prestamos, ahora)
            if vencidos:
                _auditar_vencidos(sesion, vencidos, context, ahora)
                sesion.commit()

            prestamos_creados = 0
            prestamos_aprobados = 0
            prestamos_desembolsados = 0
            capital_creado = CERO
            capital_aprobado = CERO
            capital_desembolsado = CERO
            prestamos_pagados = 0
            activos_al_cierre = 0
            saldo_al_cierre = CERO
            mora_al_cierre = CERO
            prestamos_en_mora = 0
            hoy = ahora.date()

            for prestamo in prestamos:
                if _en_rango(prestamo.created_at, desde, hasta):
                    prestamos_creados += 1
                    capital_creado += prestamo.principal_amount
                if _en_rango(prestamo.approved_at, desde, hasta):
                    prestamos_aprobados += 1
                    capital_aprobado += prestamo.principal_amount
                # El único renglón del bloque que mide plata que salió: se
                # cuenta por `disbursed_at` y no por el estado del préstamo,
                # porque un ACTIVE/PAID/DEFAULTED de hoy puede haberse
                # desembolsado en cualquier período anterior. Se desembolsa
                # `principal_amount` (lo que el cliente recibe en mano), no el
                # monto financiado: los cargos se capitalizan, no se entregan.
                if _en_rango(prestamo.disbursed_at, desde, hasta):
                    prestamos_desembolsados += 1
                    capital_desembolsado += prestamo.principal_amount

                # Un préstamo PAID no guarda su propia fecha de cancelación --
                # se la atribuye al último pago recibido, que es el hecho que
                # lo cerró. Sin ese pago (caso imposible hoy: solo se llega a
                # PAID vía RecordPayment) no se cuenta en ningún período.
                if prestamo.status == LoanStatusEnum.PAID and prestamo.payments:
                    ultimo_pago = max(pago.paid_at for pago in prestamo.payments)
                    if _en_rango(ultimo_pago, desde, hasta):
                        prestamos_pagados += 1

                if prestamo.status == LoanStatusEnum.ACTIVE:
                    activos_al_cierre += 1
                    total_programado, total_pagado = _totales_prestamo(prestamo)
                    saldo_al_cierre += max(total_programado - total_pagado, CERO)
                    _, monto_vencido, _ = _estado_pago_prestamo(prestamo, hoy)
                    mora_al_cierre += monto_vencido
                    if monto_vencido > CERO:
                        prestamos_en_mora += 1

            pagos = [
                pago
                for pago in sesion.query(LoanPayment).all()
                if _en_rango(pago.paid_at, desde, hasta)
            ]
            total_cobrado = sum((pago.amount for pago in pagos), CERO)

            return dashboard_service_pb2.GetPeriodReportResponse(
                start_date=fecha_inicio.isoformat(),
                end_date=fecha_fin.isoformat(),
                clients_registered=clientes_registrados,
                loans_created=prestamos_creados,
                loans_approved=prestamos_aprobados,
                principal_created=str(capital_creado),
                principal_approved=str(capital_aprobado),
                loans_disbursed=prestamos_desembolsados,
                principal_disbursed=str(capital_desembolsado),
                payments_count=len(pagos),
                payments_total=str(total_cobrado),
                loans_paid=prestamos_pagados,
                active_loans_at_close=activos_al_cierre,
                outstanding_at_close=str(saldo_al_cierre),
                overdue_at_close=str(mora_al_cierre),
                overdue_loans_at_close=prestamos_en_mora,
            )

    def GetClientPaymentStatusReport(self, request, context):
        """BR-DASH-003: listado nominal del estado de pago de los clientes.

        Es el complemento de GetDashboardStats, que da los mismos números
        pero sumados: acá el saldo y la mora se abren por cliente, que es lo
        que hace falta para gestionar la cobranza (a quién llamar, por cuánto
        y desde cuándo).

        Solo aparecen los clientes con **cartera viva** -- al menos un
        préstamo ACTIVE o DEFAULTED. Un cliente sin préstamos, o con todos
        pagados, no tiene estado de pago que informar y solo alargaría el
        listado. `only_overdue` lo recorta además a los que efectivamente
        deben algo.

        Los totales que devuelve son los de las filas devueltas, no los de
        toda la cartera: con `only_overdue` activo describen el subconjunto en
        mora, y por eso no tienen por qué coincidir con los de
        GetDashboardStats (que siempre mira todo).
        """
        ahora = datetime.now(timezone.utc)
        hoy = ahora.date()

        with SessionLocal() as sesion:
            prestamos = sesion.query(Loan).all()
            _, vencidos = _vencer_atrasados_y_contar_activos(prestamos, ahora)
            if vencidos:
                _auditar_vencidos(sesion, vencidos, context, ahora)
                sesion.commit()

            por_cliente: dict = {}
            for prestamo in prestamos:
                if prestamo.status in (
                    LoanStatusEnum.ACTIVE,
                    LoanStatusEnum.DEFAULTED,
                ):
                    por_cliente.setdefault(prestamo.client_id, []).append(prestamo)

            filas = []
            total_saldo = CERO
            total_mora = CERO
            clientes_en_mora = 0

            for cliente in sesion.query(Client).all():
                cartera = por_cliente.get(cliente.id)
                if not cartera:
                    continue

                activos = [p for p in cartera if p.status == LoanStatusEnum.ACTIVE]
                incumplidos = [
                    p for p in cartera if p.status == LoanStatusEnum.DEFAULTED
                ]

                saldo = CERO
                mora = CERO
                cuotas_vencidas = 0
                proxima = None  # (fecha, monto) de la cuota impaga más temprana
                for prestamo in activos:
                    total_programado, total_pagado = _totales_prestamo(prestamo)
                    saldo += max(total_programado - total_pagado, CERO)
                    _, monto_vencido, cantidad = _estado_pago_prestamo(prestamo, hoy)
                    mora += monto_vencido
                    cuotas_vencidas += cantidad
                    candidata = _proxima_cuota_impaga(prestamo)
                    if candidata is not None and (
                        proxima is None or candidata[0] < proxima[0]
                    ):
                        proxima = candidata

                # INCUMPLIDO tiene prioridad sobre la mora corriente: es un
                # estado del préstamo ya declarado por un operador
                # (MarkDefaulted), no una deducción del cronograma.
                if incumplidos:
                    estado = "INCUMPLIDO"
                elif mora > CERO:
                    estado = "CUOTA_VENCIDA"
                else:
                    estado = "AL_DIA"

                if request.only_overdue and estado == "AL_DIA":
                    continue

                if estado != "AL_DIA":
                    clientes_en_mora += 1
                total_saldo += saldo
                total_mora += mora

                filas.append(
                    dashboard_service_pb2.ClientPaymentStatusRow(
                        client_id=str(cliente.id),
                        client_name=f"{cliente.first_name} {cliente.last_name}",
                        national_id=cliente.national_id,
                        phone_number=cliente.phone_number,
                        active_loans_count=len(activos),
                        defaulted_loans_count=len(incumplidos),
                        outstanding_balance=str(saldo),
                        overdue_amount=str(mora),
                        overdue_installments_count=cuotas_vencidas,
                        next_due_date="" if proxima is None else proxima[0].isoformat(),
                        next_due_amount="" if proxima is None else str(proxima[1]),
                        payment_status=estado,
                    )
                )

            # Lo más urgente primero: mora descendente y, a igual mora, por
            # nombre, para que dos ejecuciones seguidas den el mismo orden.
            filas.sort(key=lambda f: (-Decimal(f.overdue_amount), f.client_name))

            return dashboard_service_pb2.GetClientPaymentStatusReportResponse(
                generated_at=a_marca_tiempo(ahora),
                only_overdue=request.only_overdue,
                rows=filas,
                clients_count=len(filas),
                overdue_clients_count=clientes_en_mora,
                total_outstanding=str(total_saldo),
                total_overdue=str(total_mora),
            )
