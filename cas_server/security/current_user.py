"""Per-RPC-call identity of the authenticated caller, set by AuthInterceptor.

Uses a ContextVar rather than mutating the grpc ServicerContext object so
service code doesn't depend on grpc internals. Each RPC handler execution
sets this once at entry; it is only ever read back within that same call.
"""

from contextvars import ContextVar

from cas_server.security.tokens import TokenClaims

_current_claims: ContextVar[TokenClaims | None] = ContextVar(
    "current_claims", default=None
)


def set_current_claims(claims: TokenClaims | None) -> None:
    _current_claims.set(claims)


def get_current_claims() -> TokenClaims | None:
    return _current_claims.get()
