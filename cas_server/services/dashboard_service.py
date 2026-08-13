"""Estadísticas agregadas de solo lectura para el dashboard (pantalla de
inicio del cliente) -- no persiste tablas propias, agrega sobre Client/Loan
ya existentes. Sigue la convención en español de client_service.py y
loan_service.py."""

from datetime import datetime, timezone
from decimal import Decimal

import dashboard_service_pb2
import dashboard_service_pb2_grpc
from cas_server.db.base import SessionLocal
from cas_server.db.models import Client, Loan, LoanStatusEnum

# Reutiliza el chequeo perezoso de vencimiento (BR-LOAN-003) ya implementado
# en loan_service.py en vez de duplicar esa regla acá -- mismo patrón que
# ListClientLoans/GetLoanById dentro de ese mismo módulo.
from cas_server.services.loan_service import (
    _auditar_vencidos,
    _totales_prestamo,
    _vencer_atrasados_y_contar_activos,
)

CERO = Decimal("0.00")

_ESTADOS_DESEMBOLSADOS = (
    LoanStatusEnum.ACTIVE,
    LoanStatusEnum.PAID,
    LoanStatusEnum.DEFAULTED,
)


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
            for prestamo in prestamos:
                conteos[prestamo.status] += 1
                if prestamo.status in _ESTADOS_DESEMBOLSADOS:
                    total_desembolsado += prestamo.principal_amount
                if prestamo.status == LoanStatusEnum.ACTIVE:
                    total_programado, total_pagado = _totales_prestamo(prestamo)
                    saldo_pendiente += max(total_programado - total_pagado, CERO)

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
            )
