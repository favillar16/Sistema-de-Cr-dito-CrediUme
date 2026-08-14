import uuid
from datetime import datetime, timedelta, timezone

import grpc
from google.protobuf.timestamp_pb2 import Timestamp

import auth_service_pb2
import auth_service_pb2_grpc
from cas_server import config
from cas_server.db.base import SessionLocal
from cas_server.db.models import AuditLog, RevokedToken, RoleEnum, User
from cas_server.security.current_user import get_current_claims
from cas_server.security.passwords import hash_password, verify_password
from cas_server.security.tokens import create_access_token, decode_token
from cas_server.services.common import confirmar_o_duplicado as _commit_or_conflict


def _to_timestamp(value: datetime) -> Timestamp:
    timestamp = Timestamp()
    timestamp.FromDatetime(value)
    return timestamp


def _peer_ip(context: grpc.ServicerContext) -> str:
    peer = context.peer() or ""
    # grpc peer strings look like "ipv4:127.0.0.1:54321" or "ipv6:[::1]:54321"
    if ":" in peer:
        parts = peer.split(":")
        if len(parts) >= 2:
            return parts[1]
    return peer


class AuthServicer(auth_service_pb2_grpc.AuthServiceServicer):
    def Login(self, request, context):
        if not request.username or not request.password:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT, "username and password are required"
            )

        with SessionLocal() as session:
            user = (
                session.query(User).filter_by(username=request.username).one_or_none()
            )

            if user is None:
                # Don't reveal whether the username exists.
                context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid credentials")

            now = datetime.now(timezone.utc)

            # BR-AUTH-002: lock auto-expires after LOCKOUT_DURATION_SECONDS.
            if user.is_locked and user.locked_until and user.locked_until <= now:
                user.is_locked = False
                user.locked_until = None
                user.failed_login_attempts = 0

            if user.is_locked:
                context.abort(
                    grpc.StatusCode.UNAUTHENTICATED,
                    "Account is locked due to too many failed attempts. Try again later.",
                )

            if not verify_password(request.password, user.password_hash):
                user.failed_login_attempts += 1
                action = "LOGIN_FAILED"
                if user.failed_login_attempts >= config.LOCKOUT_MAX_ATTEMPTS:
                    user.is_locked = True
                    user.locked_until = now + timedelta(
                        seconds=config.LOCKOUT_DURATION_SECONDS
                    )
                    action = "ACCOUNT_LOCKED"
                session.add(
                    AuditLog(
                        user_id=user.id,
                        action=action,
                        ip_address=_peer_ip(context),
                        timestamp=now,
                    )
                )
                session.commit()
                context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid credentials")

            user.failed_login_attempts = 0
            user.last_login = now
            token, claims = create_access_token(
                user_id=str(user.id), username=user.username, role=user.role.value
            )
            session.add(
                AuditLog(
                    user_id=user.id,
                    action="LOGIN_SUCCESS",
                    ip_address=_peer_ip(context),
                    timestamp=now,
                )
            )
            session.commit()

            return auth_service_pb2.LoginResponse(
                access_token=token,
                role=user.role.value,
                expires_in_seconds=config.JWT_EXPIRES_SECONDS,
            )

    def Logout(self, request, context):
        if not request.access_token:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "access_token is required")

        try:
            claims = decode_token(request.access_token)
        except Exception:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Invalid access_token")

        with SessionLocal() as session:
            if session.get(RevokedToken, claims.jti) is None:
                session.add(RevokedToken(jti=claims.jti, expires_at=claims.exp))
            session.add(
                AuditLog(
                    user_id=uuid.UUID(claims.user_id),
                    action="LOGOUT",
                    ip_address=_peer_ip(context),
                    timestamp=datetime.now(timezone.utc),
                )
            )
            session.commit()

        return auth_service_pb2.LogoutResponse(success=True)

    def ResetPassword(self, request, context):
        if not request.target_user_id or not request.new_password:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "target_user_id and new_password are required",
            )
        if len(request.new_password) < config.PASSWORD_MIN_LENGTH:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"new_password must be at least {config.PASSWORD_MIN_LENGTH} "
                "characters long",
            )

        try:
            target_id = uuid.UUID(request.target_user_id)
        except ValueError:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT, "target_user_id must be a valid UUID"
            )

        with SessionLocal() as session:
            target = session.get(User, target_id)
            if target is None:
                context.abort(grpc.StatusCode.NOT_FOUND, "User not found")

            target.password_hash = hash_password(request.new_password)
            target.failed_login_attempts = 0
            target.is_locked = False
            target.locked_until = None

            actor_claims = get_current_claims()
            session.add(
                AuditLog(
                    user_id=uuid.UUID(actor_claims.user_id) if actor_claims else None,
                    action=f"PASSWORD_RESET target={request.target_user_id}",
                    ip_address=_peer_ip(context),
                    timestamp=datetime.now(timezone.utc),
                )
            )
            session.commit()

        return auth_service_pb2.ResetPasswordResponse(success=True)

    def CreateUser(self, request, context):
        if not request.username or not request.password or not request.role:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "username, password and role are required",
            )
        # BR-AUTH-006: los datos personales del operador son obligatorios al
        # darlo de alta -- se imprimen como responsable en los documentos que
        # recibe el cliente. Se validan acá y no con una restricción de
        # esquema, mismo criterio que las referencias de BR-CLI-005: los
        # usuarios que ya existían siguen teniéndolos vacíos y no hizo falta
        # backfill (ver models.py).
        first_name = request.first_name.strip()
        last_name = request.last_name.strip()
        national_id = request.national_id.strip()
        if not first_name or not last_name or not national_id:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "first_name, last_name and national_id are required",
            )
        if len(request.password) < config.PASSWORD_MIN_LENGTH:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"password must be at least {config.PASSWORD_MIN_LENGTH} "
                "characters long",
            )
        try:
            role = RoleEnum(request.role)
        except ValueError:
            context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                "role must be one of: " + ", ".join(r.value for r in RoleEnum),
            )

        with SessionLocal() as session:
            existing = (
                session.query(User).filter_by(username=request.username).one_or_none()
            )
            if existing is not None:
                context.abort(
                    grpc.StatusCode.ALREADY_EXISTS, "username is already taken"
                )

            user = User(
                username=request.username,
                password_hash=hash_password(request.password),
                role=role,
                first_name=first_name,
                last_name=last_name,
                national_id=national_id,
            )
            session.add(user)
            # The INSERT (and any concurrent unique-constraint conflict)
            # fires here, not at the final commit.
            _commit_or_conflict(
                session,
                context,
                "username is already taken",
                accion=session.flush,
            )

            actor_claims = get_current_claims()
            session.add(
                AuditLog(
                    user_id=uuid.UUID(actor_claims.user_id) if actor_claims else None,
                    action=(
                        f"USER_CREATED user_id={user.id} username={request.username} "
                        f"role={role.value}"
                    ),
                    ip_address=_peer_ip(context),
                    timestamp=datetime.now(timezone.utc),
                )
            )
            session.commit()

            return auth_service_pb2.CreateUserResponse(
                user_id=str(user.id), username=user.username, role=user.role.value
            )

    def ListUsers(self, request, context):
        with SessionLocal() as session:
            users = session.query(User).order_by(User.username).all()
            summaries = []
            for user in users:
                fields = dict(
                    id=str(user.id),
                    username=user.username,
                    role=user.role.value,
                    is_locked=user.is_locked,
                    first_name=user.first_name or "",
                    last_name=user.last_name or "",
                    national_id=user.national_id or "",
                )
                if user.last_login is not None:
                    fields["last_login"] = _to_timestamp(user.last_login)
                summaries.append(auth_service_pb2.UserSummary(**fields))

        return auth_service_pb2.ListUsersResponse(users=summaries)
