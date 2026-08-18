from typing import Callable

import grpc
from PySide6.QtCore import QThread, Signal

from cas_client.grpc_client import ApiError, AuthError
from cas_client.session import (
    CONNECTION_LOST_MESSAGE,
    SESSION_EXPIRED_MESSAGE,
    session_events,
)

# Every view keeps only a single `self._worker` attribute and reassigns it
# per call (see e.g. loans_view.py) -- fine for one-off calls, but a success
# handler that itself starts another call (e.g. LoansView._on_create_success
# chaining into _run_list() then _load_detail(), both of which reassign
# self._worker) drops the only Python reference to the *previous* worker
# while its QThread may not have fully wound down yet. PySide6 then garbage
# collects the still-finishing QThread, which Qt treats as a fatal error
# ("QThread: Destroyed while thread is still running") and aborts the whole
# process -- this was the crash reported when creating a new loan. Keeping a
# strong reference here, independent of any view's own attribute, until
# `finished` actually fires (which Qt only emits once run() has returned and
# the OS thread has stopped) closes that race for every call site at once.
_ACTIVE_WORKERS: set["AsyncWorker"] = set()


class AsyncWorker(QThread):
    """Runs a single callable off the UI thread and reports back via signals.

    Generalizes the _LoginWorker pattern from login_view.py so every gRPC
    call site doesn't need its own bespoke QThread subclass -- per ES-003 §5,
    every call from the UI thread must run on a worker thread with errors
    translated to a friendly message before reaching the view.
    """

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        fn: Callable,
        *args,
        error_translator: Callable[[Exception], str] | None = None,
        **kwargs,
    ):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._error_translator = error_translator or (lambda exc: str(exc))
        _ACTIVE_WORKERS.add(self)
        self.finished.connect(self._release)

    def _release(self) -> None:
        _ACTIVE_WORKERS.discard(self)
        self.deleteLater()

    def run(self) -> None:
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as exc:  # noqa: BLE001 -- translated below, never re-raised
            if _is_expired_session(exc):
                # Handled here rather than in each view's own
                # _friendly_message(): this is the single choke point every
                # authenticated gRPC call in the client already goes through,
                # so one check covers all ~25 call sites at once and can't
                # drift out of sync the way five hand-maintained translators
                # would. LoginView deliberately does NOT go through here (it
                # has its own _LoginWorker), which is what keeps a genuine
                # wrong-password UNAUTHENTICATED from being misreported as an
                # expired session.
                session_events.expired.emit()
                self.failed.emit(SESSION_EXPIRED_MESSAGE)
                return
            if _is_unreachable(exc):
                # Mismo criterio que la expiración de sesión de arriba, y por
                # el mismo motivo: DEADLINE_EXCEEDED sólo empezó a ser posible
                # cuando las llamadas pasaron a tener un plazo (ver
                # grpc_client._invoke), y ningún _friendly_message() de las
                # vistas lo contempla -- todos lo dejarían caer en su
                # "Ocurrió un error inesperado", que es justo lo que no hay
                # que decirle a un operador cuyo problema es de red. Resolverlo
                # acá cubre los ~30 call sites de una vez en lugar de sumar la
                # misma rama a media docena de traductores que después se
                # desincronizan.
                self.failed.emit(CONNECTION_LOST_MESSAGE)
                return
            self.failed.emit(self._error_translator(exc))
            return
        self.succeeded.emit(result)


def _is_unreachable(exc: Exception) -> bool:
    """True cuando la llamada no llegó a completarse por un problema de red.

    `DEADLINE_EXCEEDED` es el caso nuevo: con el plazo por llamada que fija
    grpc_client._invoke, un servidor inalcanzable ahora corta a los 20 s en
    vez de colgar el worker para siempre. Se trata igual que `UNAVAILABLE`
    porque para el operador son el mismo hecho -- el servidor no contestó --
    y en ninguno de los dos casos la operación quedó registrada.
    """
    return isinstance(exc, (ApiError, AuthError)) and exc.code in (
        grpc.StatusCode.DEADLINE_EXCEEDED,
        grpc.StatusCode.UNAVAILABLE,
    )


def _is_expired_session(exc: Exception) -> bool:
    """True when the server rejected the call because our token is no longer
    usable. AuthInterceptor answers UNAUTHENTICATED for all of: missing
    bearer token, expired token, malformed token, revoked token (post-logout)
    and unknown role -- every one of them means "this session is over, log in
    again", so they're deliberately not distinguished here."""
    return (
        isinstance(exc, (ApiError, AuthError))
        and exc.code == grpc.StatusCode.UNAUTHENTICATED
    )
