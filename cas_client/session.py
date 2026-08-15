from PySide6.QtCore import QObject, Signal

# BR-AUTH-003: the JWT lasts 8 hours with no refresh token, so a client left
# open across a full workday *will* hit this -- it's the expected end of a
# session, not an anomaly. Shown verbatim by every view (via AsyncWorker) and
# again on the login screen once the app returns to it.
SESSION_EXPIRED_MESSAGE = "Su sesión expiró. Vuelva a iniciar sesión."


class _SessionEvents(QObject):
    """App-wide notifier for "the token this client holds is no longer usable".

    A module-level singleton rather than a signal on Session itself: Session
    is a plain data holder passed into views at construction, while the only
    interested listener is MainWindow (which owns the login/shell stack). The
    signal is emitted from AsyncWorker's *worker* thread; since this object
    lives on the main thread, Qt delivers it as a queued connection, so the
    slot still runs on the UI thread where switching views is safe.
    """

    expired = Signal()


session_events = _SessionEvents()


class Session:
    """Shared, mutable holder for the post-login access token/role/username.

    Views that need to make authenticated calls (ClientsView, LoansView) are
    constructed once, before login happens, and hold a reference to this
    object rather than the token itself -- MainWindow updates it in place on
    login/logout so every view always sees the current value.
    """

    def __init__(self):
        self.access_token: str | None = None
        self.role: str | None = None
        self.username: str | None = None

    def clear(self) -> None:
        self.access_token = None
        self.role = None
        self.username = None
