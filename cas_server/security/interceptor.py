import grpc
import jwt

from cas_server.db.base import SessionLocal
from cas_server.db.models import RevokedToken, RoleEnum
from cas_server.security import rbac
from cas_server.security.current_user import set_current_claims
from cas_server.security.tokens import decode_token


def _abort_handler(code: grpc.StatusCode, message: str):
    def behavior(request, context):
        context.abort(code, message)

    return grpc.unary_unary_rpc_method_handler(behavior)


def _extract_token(invocation_metadata) -> str | None:
    metadata = dict(invocation_metadata or [])
    header = metadata.get("authorization", "")
    if not header.startswith("Bearer "):
        return None
    return header[len("Bearer ") :]


def _is_revoked(jti: str) -> bool:
    with SessionLocal() as session:
        return session.get(RevokedToken, jti) is not None


class AuthInterceptor(grpc.ServerInterceptor):
    """BR-AUTH-004: validates the JWT and enforces per-method RBAC.

    Public methods (rbac.PUBLIC_METHODS) pass through untouched. Everything
    else requires a valid, non-revoked "authorization: Bearer <token>"
    metadata entry, and the token's role must be in rbac.allowed_roles(method).
    """

    def intercept_service(self, continuation, handler_call_details):
        method = handler_call_details.method

        if rbac.is_public(method):
            return continuation(handler_call_details)

        allowed = rbac.allowed_roles(method)
        if not allowed:
            return _abort_handler(
                grpc.StatusCode.UNIMPLEMENTED, f"Unknown method {method}"
            )

        token = _extract_token(handler_call_details.invocation_metadata)
        if token is None:
            return _abort_handler(
                grpc.StatusCode.UNAUTHENTICATED, "Missing bearer token"
            )

        try:
            claims = decode_token(token)
        except jwt.ExpiredSignatureError:
            return _abort_handler(grpc.StatusCode.UNAUTHENTICATED, "Token expired")
        except jwt.PyJWTError:
            return _abort_handler(grpc.StatusCode.UNAUTHENTICATED, "Invalid token")

        if _is_revoked(claims.jti):
            return _abort_handler(grpc.StatusCode.UNAUTHENTICATED, "Token revoked")

        try:
            role = RoleEnum(claims.role)
        except ValueError:
            return _abort_handler(
                grpc.StatusCode.UNAUTHENTICATED, "Invalid role in token"
            )

        if role not in allowed:
            return _abort_handler(
                grpc.StatusCode.PERMISSION_DENIED,
                f"Role {role.value} is not permitted to call {method}",
            )

        handler = continuation(handler_call_details)
        if handler is None or not handler.unary_unary:
            return handler

        inner_behavior = handler.unary_unary

        def behavior(request, context):
            set_current_claims(claims)
            try:
                return inner_behavior(request, context)
            finally:
                set_current_claims(None)

        return grpc.unary_unary_rpc_method_handler(
            behavior,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )
