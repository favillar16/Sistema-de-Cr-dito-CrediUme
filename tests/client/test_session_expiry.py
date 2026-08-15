"""Cobertura del manejo de sesión vencida (BR-AUTH-003: JWT de 8 horas sin
refresh, así que una app abierta toda la jornada lo va a alcanzar).

Funciones puras: no levantan QApplication, no abren canales gRPC y no tocan
la base de datos.
"""

import grpc

from cas_client.grpc_client import ApiError, AuthError
from cas_client.session import SESSION_EXPIRED_MESSAGE, Session
from cas_client.views.login_view import _friendly_message as login_message
from cas_client.widgets.async_worker import _is_expired_session


def test_unauthenticated_from_any_client_is_treated_as_an_expired_session():
    """AuthInterceptor responde UNAUTHENTICATED para token ausente, vencido,
    inválido, revocado y rol desconocido -- todos significan lo mismo para el
    operador, así que no se distinguen."""
    assert _is_expired_session(
        ApiError(grpc.StatusCode.UNAUTHENTICATED, "Token expired")
    )
    assert _is_expired_session(
        AuthError(grpc.StatusCode.UNAUTHENTICATED, "Token revoked")
    )


def test_other_grpc_errors_are_not_mistaken_for_an_expired_session():
    """Lo importante acá es UNAVAILABLE: si se colara, un servidor caído
    expulsaría al operador a la pantalla de login en vez de decirle que
    reintente, perdiendo el formulario que estuviera cargando."""
    for code in (
        grpc.StatusCode.UNAVAILABLE,
        grpc.StatusCode.PERMISSION_DENIED,
        grpc.StatusCode.NOT_FOUND,
        grpc.StatusCode.INVALID_ARGUMENT,
        grpc.StatusCode.FAILED_PRECONDITION,
        grpc.StatusCode.ALREADY_EXISTS,
    ):
        assert not _is_expired_session(ApiError(code, "x")), code


def test_non_grpc_exceptions_are_not_mistaken_for_an_expired_session():
    assert not _is_expired_session(ValueError("boom"))
    assert not _is_expired_session(OSError("archivo bloqueado"))


def test_login_still_reports_unauthenticated_as_bad_credentials():
    """LoginView no pasa por AsyncWorker (tiene su propio _LoginWorker) justo
    para esto: en Login, UNAUTHENTICATED significa contraseña equivocada o
    cuenta bloqueada, no sesión vencida. Si alguien unificara ambos caminos,
    un usuario tipeando mal la contraseña leería 'su sesión expiró'."""
    mensaje = login_message(
        AuthError(grpc.StatusCode.UNAUTHENTICATED, "Invalid credentials")
    )
    assert mensaje == "Usuario o contraseña incorrectos, o la cuenta está bloqueada."
    assert mensaje != SESSION_EXPIRED_MESSAGE


def test_session_clear_drops_every_credential_field():
    """_on_session_expired() se apoya en que clear() deja access_token en None:
    ese None es el guard que evita que varias llamadas fallando a la vez
    desmonten la sesión más de una vez."""
    session = Session()
    session.access_token = "token"
    session.role = "ADMIN"
    session.username = "operador"

    session.clear()

    assert session.access_token is None
    assert session.role is None
    assert session.username is None
