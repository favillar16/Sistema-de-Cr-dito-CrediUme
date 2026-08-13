from datetime import date, datetime, timezone

import grpc
from sqlalchemy import or_

import client_service_pb2
import client_service_pb2_grpc
from cas_server.db.base import SessionLocal
from cas_server.db.models import AuditLog, Client, Loan, LoanStatusEnum
from cas_server.security.current_user import get_current_claims
from cas_server.services.common import (
    analizar_decimal,
    analizar_fecha,
    analizar_uuid,
    confirmar_o_duplicado,
    id_actor_actual,
    ip_remota,
    a_marca_tiempo,
)

TAMANO_PAGINA_POR_DEFECTO = 20


def _edad_anios(fecha_nacimiento: date, hoy: date) -> int:
    anios = hoy.year - fecha_nacimiento.year
    if (hoy.month, hoy.day) < (fecha_nacimiento.month, fecha_nacimiento.day):
        anios -= 1
    return anios


def _perfil_extendido_kwargs(request, context) -> dict:
    """BR-CLI-007: parsea los 7 campos opcionales de perfil extendido, comunes a
    CreateClient y UpdateClient. Vacío ("") equivale a no informado (None)."""
    vencimiento_cedula = None
    if request.national_id_expiry_date:
        vencimiento_cedula = analizar_fecha(
            request.national_id_expiry_date, "national_id_expiry_date", context
        )
    return dict(
        national_id_expiry_date=vencimiento_cedula,
        marital_status=request.marital_status.strip() or None,
        education_level=request.education_level.strip() or None,
        occupation=request.occupation.strip() or None,
        neighborhood=request.neighborhood.strip() or None,
        risk_rating=request.risk_rating.strip() or None,
        economic_sector=request.economic_sector.strip() or None,
    )


def _cliente_a_respuesta(cliente: Client) -> client_service_pb2.GetClientByIdResponse:
    ingreso = (
        ""
        if cliente.declared_monthly_income is None
        else str(cliente.declared_monthly_income)
    )
    return client_service_pb2.GetClientByIdResponse(
        id=str(cliente.id),
        first_name=cliente.first_name,
        last_name=cliente.last_name,
        national_id=cliente.national_id,
        email=cliente.email,
        phone_number=cliente.phone_number,
        is_active=cliente.is_active,
        created_at=a_marca_tiempo(cliente.created_at),
        date_of_birth=cliente.date_of_birth.isoformat(),
        address=cliente.address,
        updated_at=a_marca_tiempo(cliente.updated_at),
        declared_monthly_income=ingreso,
        personal_reference_1_name=cliente.personal_reference_1_name or "",
        personal_reference_1_relationship=cliente.personal_reference_1_relationship
        or "",
        personal_reference_1_phone=cliente.personal_reference_1_phone or "",
        personal_reference_2_name=cliente.personal_reference_2_name or "",
        personal_reference_2_relationship=cliente.personal_reference_2_relationship
        or "",
        personal_reference_2_phone=cliente.personal_reference_2_phone or "",
        employment_reference_employer=cliente.employment_reference_employer or "",
        employment_reference_position=cliente.employment_reference_position or "",
        employment_reference_phone=cliente.employment_reference_phone or "",
        employment_reference_seniority=cliente.employment_reference_seniority or "",
        source_of_funds=cliente.source_of_funds or "",
        national_id_expiry_date=(
            cliente.national_id_expiry_date.isoformat()
            if cliente.national_id_expiry_date
            else ""
        ),
        marital_status=cliente.marital_status or "",
        education_level=cliente.education_level or "",
        occupation=cliente.occupation or "",
        neighborhood=cliente.neighborhood or "",
        risk_rating=cliente.risk_rating or "",
        economic_sector=cliente.economic_sector or "",
    )


class ClientServicer(client_service_pb2_grpc.ClientServiceServicer):
    def CreateClient(self, request, context):
        campos_requeridos = (
            request.first_name,
            request.last_name,
            request.national_id,
            request.email,
            request.phone_number,
            request.date_of_birth,
            request.address,
        )
        if not all(campos_requeridos):
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "first_name, last_name, national_id, email, phone_number, "
                "date_of_birth y address son obligatorios",
            )

        referencias_requeridas = (
            request.personal_reference_1_name,
            request.personal_reference_1_relationship,
            request.personal_reference_1_phone,
            request.personal_reference_2_name,
            request.personal_reference_2_relationship,
            request.personal_reference_2_phone,
            request.employment_reference_employer,
            request.employment_reference_position,
            request.employment_reference_phone,
            request.employment_reference_seniority,
        )
        if not all(referencias_requeridas):
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "las referencias personales (nombre, parentesco, teléfono x2) y "
                "la referencia laboral (empleador, cargo, teléfono, antigüedad) "
                "son obligatorias (BR-CLI-005)",
            )

        if not request.source_of_funds:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "el origen de fondos es obligatorio (BR-CLI-006)",
            )

        try:
            fecha_nacimiento = date.fromisoformat(request.date_of_birth)
        except ValueError:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "date_of_birth debe tener el formato AAAA-MM-DD",
            )

        ahora = datetime.now(timezone.utc)
        if _edad_anios(fecha_nacimiento, ahora.date()) < 18:
            context.abort(
                grpc.StatusCode.FAILED_PRECONDITION,
                "El cliente debe tener al menos 18 años (BR-CLI-002)",
            )

        ingreso = None
        if request.HasField("declared_monthly_income"):
            ingreso = analizar_decimal(
                request.declared_monthly_income, "declared_monthly_income", context
            )

        perfil_extendido = _perfil_extendido_kwargs(request, context)

        with SessionLocal() as sesion:
            existente = (
                sesion.query(Client)
                .filter(
                    or_(
                        Client.national_id == request.national_id,
                        Client.email == request.email,
                    )
                )
                .first()
            )
            if existente is not None:
                campo = (
                    "national_id"
                    if existente.national_id == request.national_id
                    else "email"
                )
                context.abort(
                    grpc.StatusCode.ALREADY_EXISTS, f"{campo} ya está registrado"
                )

            cliente = Client(
                first_name=request.first_name,
                last_name=request.last_name,
                national_id=request.national_id,
                email=request.email,
                phone_number=request.phone_number,
                address=request.address,
                date_of_birth=fecha_nacimiento,
                declared_monthly_income=ingreso,
                is_active=True,
                created_at=ahora,
                updated_at=ahora,
                personal_reference_1_name=request.personal_reference_1_name,
                personal_reference_1_relationship=request.personal_reference_1_relationship,
                personal_reference_1_phone=request.personal_reference_1_phone,
                personal_reference_2_name=request.personal_reference_2_name,
                personal_reference_2_relationship=request.personal_reference_2_relationship,
                personal_reference_2_phone=request.personal_reference_2_phone,
                employment_reference_employer=request.employment_reference_employer,
                employment_reference_position=request.employment_reference_position,
                employment_reference_phone=request.employment_reference_phone,
                employment_reference_seniority=request.employment_reference_seniority,
                source_of_funds=request.source_of_funds,
                **perfil_extendido,
            )
            sesion.add(cliente)
            # El INSERT (y su posible violación de unicidad concurrente) se
            # dispara acá, no en el commit final -- por eso el flush() es lo
            # que hay que envolver, no solo sesion.commit(). Ver el
            # docstring de confirmar_o_duplicado.
            confirmar_o_duplicado(
                sesion,
                context,
                "national_id o email ya está registrado",
                accion=sesion.flush,
            )

            sesion.add(
                AuditLog(
                    user_id=id_actor_actual(get_current_claims()),
                    action=f"CLIENTE_CREADO client_id={cliente.id}",
                    ip_address=ip_remota(context),
                    timestamp=ahora,
                )
            )
            sesion.commit()

            return client_service_pb2.CreateClientResponse(
                client_id=str(cliente.id), created_at=a_marca_tiempo(cliente.created_at)
            )

    def GetClientById(self, request, context):
        client_id = analizar_uuid(request.client_id, "client_id", context)

        with SessionLocal() as sesion:
            cliente = sesion.get(Client, client_id)
            if cliente is None:
                context.abort(grpc.StatusCode.NOT_FOUND, "Cliente no encontrado")
            return _cliente_a_respuesta(cliente)

    def SearchClients(self, request, context):
        tamano_pagina = (
            request.page_size if request.page_size > 0 else TAMANO_PAGINA_POR_DEFECTO
        )
        token_pagina = request.page_token if request.page_token > 0 else 0
        termino = request.search_term.strip()

        with SessionLocal() as sesion:
            consulta = sesion.query(Client)
            if termino:
                patron = f"%{termino}%"
                consulta = consulta.filter(
                    or_(
                        Client.first_name.ilike(patron),
                        Client.last_name.ilike(patron),
                        Client.national_id.ilike(patron),
                        Client.phone_number.ilike(patron),
                    )
                )
            resultados = (
                consulta.order_by(Client.last_name, Client.first_name)
                .offset(token_pagina)
                .limit(tamano_pagina)
                .all()
            )

            siguiente_token_pagina = (
                token_pagina + len(resultados)
                if len(resultados) == tamano_pagina
                else 0
            )
            return client_service_pb2.SearchClientsResponse(
                clients=[_cliente_a_respuesta(c) for c in resultados],
                next_page_token=siguiente_token_pagina,
            )

    def UpdateClient(self, request, context):
        client_id = analizar_uuid(request.client_id, "client_id", context)

        if not (request.email and request.phone_number and request.address):
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "email, phone_number y address son obligatorios",
            )

        referencias_requeridas = (
            request.personal_reference_1_name,
            request.personal_reference_1_relationship,
            request.personal_reference_1_phone,
            request.personal_reference_2_name,
            request.personal_reference_2_relationship,
            request.personal_reference_2_phone,
            request.employment_reference_employer,
            request.employment_reference_position,
            request.employment_reference_phone,
            request.employment_reference_seniority,
        )
        if not all(referencias_requeridas):
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "las referencias personales (nombre, parentesco, teléfono x2) y "
                "la referencia laboral (empleador, cargo, teléfono, antigüedad) "
                "son obligatorias (BR-CLI-005)",
            )

        if not request.source_of_funds:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "el origen de fondos es obligatorio (BR-CLI-006)",
            )

        ingreso = None
        if request.HasField("declared_monthly_income"):
            ingreso = analizar_decimal(
                request.declared_monthly_income, "declared_monthly_income", context
            )

        perfil_extendido = _perfil_extendido_kwargs(request, context)

        with SessionLocal() as sesion:
            cliente = sesion.get(Client, client_id)
            if cliente is None:
                context.abort(grpc.StatusCode.NOT_FOUND, "Cliente no encontrado")

            if request.email != cliente.email:
                conflicto = (
                    sesion.query(Client)
                    .filter(Client.email == request.email, Client.id != cliente.id)
                    .first()
                )
                if conflicto is not None:
                    context.abort(
                        grpc.StatusCode.ALREADY_EXISTS, "el email ya está registrado"
                    )

            cliente.email = request.email
            cliente.phone_number = request.phone_number
            cliente.address = request.address
            if request.HasField("declared_monthly_income"):
                cliente.declared_monthly_income = ingreso
            cliente.personal_reference_1_name = request.personal_reference_1_name
            cliente.personal_reference_1_relationship = (
                request.personal_reference_1_relationship
            )
            cliente.personal_reference_1_phone = request.personal_reference_1_phone
            cliente.personal_reference_2_name = request.personal_reference_2_name
            cliente.personal_reference_2_relationship = (
                request.personal_reference_2_relationship
            )
            cliente.personal_reference_2_phone = request.personal_reference_2_phone
            cliente.employment_reference_employer = (
                request.employment_reference_employer
            )
            cliente.employment_reference_position = (
                request.employment_reference_position
            )
            cliente.employment_reference_phone = request.employment_reference_phone
            cliente.employment_reference_seniority = (
                request.employment_reference_seniority
            )
            cliente.source_of_funds = request.source_of_funds
            for campo, valor in perfil_extendido.items():
                setattr(cliente, campo, valor)
            cliente.updated_at = datetime.now(timezone.utc)

            sesion.add(
                AuditLog(
                    user_id=id_actor_actual(get_current_claims()),
                    action=f"CLIENTE_ACTUALIZADO client_id={cliente.id}",
                    ip_address=ip_remota(context),
                    timestamp=cliente.updated_at,
                )
            )
            confirmar_o_duplicado(sesion, context, "el email ya está registrado")

            return client_service_pb2.UpdateClientResponse(
                success=True, updated_at=a_marca_tiempo(cliente.updated_at)
            )

    def DeactivateClient(self, request, context):
        client_id = analizar_uuid(request.client_id, "client_id", context)

        with SessionLocal() as sesion:
            cliente = sesion.get(Client, client_id)
            if cliente is None:
                context.abort(grpc.StatusCode.NOT_FOUND, "Cliente no encontrado")

            if not cliente.is_active:
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION, "El cliente ya está inactivo"
                )

            tiene_prestamos_no_pagados = (
                sesion.query(Loan)
                .filter(
                    Loan.client_id == cliente.id, Loan.status != LoanStatusEnum.PAID
                )
                .first()
                is not None
            )
            if tiene_prestamos_no_pagados:
                context.abort(
                    grpc.StatusCode.FAILED_PRECONDITION,
                    "El cliente tiene préstamos que no están completamente "
                    "pagados (BR-CLI-004)",
                )

            cliente.is_active = False
            cliente.updated_at = datetime.now(timezone.utc)

            sesion.add(
                AuditLog(
                    user_id=id_actor_actual(get_current_claims()),
                    action=f"CLIENTE_DESACTIVADO client_id={cliente.id}",
                    ip_address=ip_remota(context),
                    timestamp=cliente.updated_at,
                )
            )
            sesion.commit()

            return client_service_pb2.DeactivateClientResponse(
                success=True, updated_at=a_marca_tiempo(cliente.updated_at)
            )

    def UpdateNationalId(self, request, context):
        client_id = analizar_uuid(request.client_id, "client_id", context)
        if not request.new_national_id:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT, "new_national_id es obligatorio"
            )

        with SessionLocal() as sesion:
            cliente = sesion.get(Client, client_id)
            if cliente is None:
                context.abort(grpc.StatusCode.NOT_FOUND, "Cliente no encontrado")

            conflicto = (
                sesion.query(Client)
                .filter(
                    Client.national_id == request.new_national_id,
                    Client.id != cliente.id,
                )
                .first()
            )
            if conflicto is not None:
                context.abort(
                    grpc.StatusCode.ALREADY_EXISTS, "el national_id ya está registrado"
                )

            documento_anterior = cliente.national_id
            cliente.national_id = request.new_national_id
            cliente.updated_at = datetime.now(timezone.utc)

            sesion.add(
                AuditLog(
                    user_id=id_actor_actual(get_current_claims()),
                    action=(
                        f"CLIENTE_DOCUMENTO_CAMBIADO client_id={cliente.id} "
                        f"anterior={documento_anterior} nuevo={request.new_national_id}"
                    ),
                    ip_address=ip_remota(context),
                    timestamp=cliente.updated_at,
                )
            )
            confirmar_o_duplicado(sesion, context, "el national_id ya está registrado")

            return client_service_pb2.UpdateNationalIdResponse(
                success=True, updated_at=a_marca_tiempo(cliente.updated_at)
            )
