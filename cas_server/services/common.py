"""Utilidades compartidas por client_service.py y loan_service.py."""

import uuid
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import grpc
from google.protobuf.timestamp_pb2 import Timestamp
from sqlalchemy.exc import IntegrityError


def ip_remota(contexto: grpc.ServicerContext) -> str:
    """Extrae la IP del llamador de un peer string con formato "ipv4:127.0.0.1:54321"."""
    peer = contexto.peer() or ""
    if ":" in peer:
        partes = peer.split(":")
        if len(partes) >= 2:
            return partes[1]
    return peer


def a_marca_tiempo(valor: datetime) -> Timestamp:
    marca_tiempo = Timestamp()
    marca_tiempo.FromDatetime(valor)
    return marca_tiempo


def id_actor_actual(credenciales) -> uuid.UUID | None:
    return uuid.UUID(credenciales.user_id) if credenciales is not None else None


def analizar_uuid(
    valor: str, nombre_campo: str, contexto: grpc.ServicerContext
) -> uuid.UUID:
    try:
        return uuid.UUID(valor)
    except (ValueError, AttributeError, TypeError):
        contexto.abort(
            grpc.StatusCode.INVALID_ARGUMENT, f"{nombre_campo} debe ser un UUID válido"
        )


def analizar_decimal(
    valor: str,
    nombre_campo: str,
    contexto: grpc.ServicerContext,
    *,
    permitir_cero: bool = True,
) -> Decimal:
    try:
        valor_analizado = Decimal(valor)
    except (InvalidOperation, ValueError, TypeError):
        contexto.abort(
            grpc.StatusCode.INVALID_ARGUMENT,
            f"{nombre_campo} debe ser un número decimal válido",
        )
        return  # pragma: no cover -- context.abort siempre lanza una excepción

    if valor_analizado < 0 or (valor_analizado == 0 and not permitir_cero):
        contexto.abort(
            grpc.StatusCode.INVALID_ARGUMENT,
            f"{nombre_campo} debe ser un número positivo",
        )
    return valor_analizado


def confirmar_o_duplicado(
    sesion,
    contexto: grpc.ServicerContext,
    mensaje_duplicado: str,
    *,
    accion=None,
) -> None:
    """Ejecuta `accion` (por defecto `sesion.commit`), traduciendo un
    `IntegrityError` de violación de restricción única (una fila duplicada
    que pasó la validación previa por una condición de carrera: dos
    requests concurrentes que hacen su propio SELECT-de-verificación antes
    de que cualquiera haga commit) a `ALREADY_EXISTS` en vez de dejar que se
    propague como un error genérico (`INTERNAL`/`UNKNOWN`). La restricción
    única de la base de datos ya garantiza que el dato no queda duplicado --
    esto solo corrige el código de estado que recibe el request perdedor.

    Pasar `accion=sesion.flush` cuando el caller necesita un `flush()`
    explícito antes de `commit()` (p. ej. para obtener un id autogenerado
    con el que armar un `AuditLog`) -- un INSERT con conflicto de unicidad
    falla en ese `flush()`, no en el `commit()` posterior, así que envolver
    solo el commit final no alcanza en ese caso. Ver ES-006 §3.1."""
    accion = accion or sesion.commit
    try:
        accion()
    except IntegrityError:
        sesion.rollback()
        contexto.abort(grpc.StatusCode.ALREADY_EXISTS, mensaje_duplicado)


def analizar_fecha(
    valor: str, nombre_campo: str, contexto: grpc.ServicerContext
) -> date:
    try:
        return date.fromisoformat(valor)
    except (ValueError, TypeError):
        contexto.abort(
            grpc.StatusCode.INVALID_ARGUMENT,
            f"{nombre_campo} debe tener el formato AAAA-MM-DD",
        )
