import grpc
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from cas_client import assets, theme
from cas_client.grpc_client import AuthClient, AuthError
from cas_client.session import SESSION_EXPIRED_MESSAGE
from cas_client.widgets.async_worker import _ACTIVE_WORKERS
from cas_client.widgets.card import labeled_field
from cas_client.widgets.toast import Toast


def _friendly_message(exc: AuthError) -> str:
    if exc.code == grpc.StatusCode.UNAUTHENTICATED:
        return "Usuario o contraseña incorrectos, o la cuenta está bloqueada."
    if exc.code == grpc.StatusCode.INVALID_ARGUMENT:
        return "Debe ingresar usuario y contraseña."
    if exc.code in (
        grpc.StatusCode.UNAVAILABLE,
        # Posible desde que las llamadas llevan plazo (grpc_client._invoke).
        # LoginView tiene su propio worker y no pasa por AsyncWorker, así que
        # esta rama no la cubre el manejo centralizado de allá -- es la misma
        # excepción deliberada que ya existe para UNAUTHENTICATED, que acá
        # significa "credenciales incorrectas" y no "sesión vencida".
        grpc.StatusCode.DEADLINE_EXCEEDED,
    ):
        return (
            "No se pudo conectar con el servidor. Verifique que el equipo "
            "servidor esté encendido y conectado a la red."
        )
    return "Ocurrió un error al iniciar sesión. Intente nuevamente."


class _LoginWorker(QThread):
    succeeded = Signal(object)  # auth_service_pb2.LoginResponse
    failed = Signal(str)

    def __init__(self, client: AuthClient, username: str, password: str):
        super().__init__()
        self._client = client
        self._username = username
        self._password = password
        # A bespoke QThread predating widgets/async_worker.py's AsyncWorker
        # (which centralized this same fix for every other view) -- shares
        # its keep-alive registry so a rapid double-click on "Ingresar"
        # can't drop the only reference to a still-finishing worker and
        # crash the app the same way loan creation did (see AsyncWorker's
        # own docstring for the full mechanism).
        _ACTIVE_WORKERS.add(self)
        self.finished.connect(self._release)

    def _release(self) -> None:
        _ACTIVE_WORKERS.discard(self)
        self.deleteLater()

    def run(self) -> None:
        try:
            response = self._client.login(self._username, self._password)
        except AuthError as exc:
            self.failed.emit(_friendly_message(exc))
            return
        except Exception as exc:  # transport-level failure (server unreachable, etc.)
            self.failed.emit(f"No se pudo conectar con el servidor: {exc}")
            return
        self.succeeded.emit(response)


class _BrandPanel(QWidget):
    """Left-hand navy panel: gradient fill, an oversized low-opacity print of
    the brand's own chart-and-arrow mark bleeding off the bottom-right
    corner as a signature texture, and a crisp logo lockup + tagline drawn
    on top via ordinary child widgets. Everything here derives from the
    already-established brand tokens (theme.py, incl. theme.BRAND_NAME) --
    no new palette was introduced, only a composition built from them."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        # No explicit setMinimumWidth() here -- Qt derives the panel's real
        # minimum from its own layout/children (icon + wordmark + margins),
        # now that the wordmark is pinned via QSizePolicy.Minimum below. An
        # explicit floor smaller than that would just lie to the outer
        # QHBoxLayout about how small this panel can safely go, and it would
        # get squeezed below what its own content needs -- which is exactly
        # what clipped the wordmark before this was removed.
        self._mark = assets.logo_mark_pixmap()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 24, 32)
        layout.setSpacing(0)

        lockup_row = QHBoxLayout()
        lockup_row.setSpacing(10)
        icon_label = QLabel()
        if not self._mark.isNull():
            icon_label.setPixmap(
                self._mark.scaledToHeight(
                    36, Qt.TransformationMode.SmoothTransformation
                )
            )
        lockup_row.addWidget(icon_label)
        wordmark = QLabel(theme.BRAND_NAME)
        wordmark.setStyleSheet(
            f"color: white; font-size: 24px; font-weight: 700; "
            f"font-family: {theme.HEADING_FONT_FAMILY};"
        )
        # QLabel's default size policy lets a QHBoxLayout compress it below
        # its own text width under tight space, even with a trailing
        # addStretch() -- Minimum pins sizeHint() as a hard floor so the
        # wordmark can't get silently clipped at the window's minimum width.
        wordmark.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        lockup_row.addWidget(wordmark)
        lockup_row.addStretch()
        layout.addLayout(lockup_row)

        layout.addStretch()

        rule = QFrame()
        rule.setFixedSize(44, 4)
        rule.setStyleSheet(f"background-color: {theme.ACCENT}; border-radius: 2px;")
        layout.addWidget(rule)

        layout.addSpacing(14)
        tagline = QLabel("Sistema de Administración de Créditos")
        tagline.setWordWrap(True)
        tagline.setStyleSheet(
            f"color: white; font-size: 18px; font-weight: 600; "
            f"font-family: {theme.HEADING_FONT_FAMILY};"
        )
        layout.addWidget(tagline)

        layout.addSpacing(6)
        subcopy = QLabel("Gestión de clientes, préstamos y cobranzas en un solo lugar.")
        subcopy.setWordWrap(True)
        subcopy.setStyleSheet(
            f"color: {theme.ON_DARK_MUTED}; font-size: 13px; "
            f"font-family: {theme.BODY_FONT_FAMILY};"
        )
        layout.addWidget(subcopy)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        gradient = QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0, QColor(theme.PRIMARY))
        # Darkened PRIMARY (not a separate hardcoded hex) so the gradient
        # endpoint is derived from the real palette rather than invented.
        gradient.setColorAt(1, QColor(theme.PRIMARY).darker(150))
        painter.fillRect(self.rect(), gradient)

        if not self._mark.isNull():
            target_width = int(self.width() * 1.9)
            scaled = self._mark.scaledToWidth(
                target_width, Qt.TransformationMode.SmoothTransformation
            )
            # Bleeds off the bottom-right corner -- an off-center, oversized
            # crop reads as an atmospheric texture rather than a second,
            # redundant logo competing with the crisp lockup above.
            x = self.width() - int(scaled.width() * 0.62)
            y = self.height() - int(scaled.height() * 0.58)
            painter.setOpacity(0.10)
            painter.drawPixmap(x, y, scaled)
            painter.setOpacity(1.0)
        super().paintEvent(event)


class LoginView(QWidget):
    """Split login screen: a branded navy panel (left) and the credentials
    form (right), replacing the old plain-gray-background centered card
    for a more deliberate, professional first impression."""

    # username, access_token, role, expires_in_seconds
    login_succeeded = Signal(str, str, str, int)

    def __init__(self, client: AuthClient, parent: QWidget | None = None):
        super().__init__(parent)
        self._client = client
        self._worker: _LoginWorker | None = None
        self._pending_username = ""

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        brand_panel = _BrandPanel()
        root.addWidget(brand_panel, stretch=44)

        form_panel = QWidget()
        form_panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        form_panel.setStyleSheet(f"background-color: {theme.APP_BACKGROUND};")
        form_layout = QVBoxLayout(form_panel)
        form_layout.setContentsMargins(24, 24, 24, 24)
        form_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(form_panel, stretch=56)

        card = QFrame()
        card.setObjectName("loginCard")
        # Wide enough that "Iniciar sesión" at the heading's font size never
        # gets squeezed below its own text width (verified via
        # QFontMetrics: needs ~308px at 22px bold, so 380 leaves comfortable
        # room even after the card's own margins).
        card.setMinimumWidth(380)
        card.setMaximumWidth(420)
        card.setStyleSheet(
            f"#loginCard {{ background: white; border-radius: 12px; "
            f"border: 1px solid {theme.BORDER}; }}"
        )
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 6)
        # Same soft, navy-tinted shadow as every other card() in the app
        # (theme.CARD_SHADOW_RGBA) rather than a bespoke flat-black one.
        shadow.setColor(QColor(*theme.CARD_SHADOW_RGBA))
        card.setGraphicsEffect(shadow)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(32, 32, 32, 32)
        card_layout.setSpacing(6)

        heading = QLabel("Iniciar sesión")
        heading.setStyleSheet(
            f"font-size: 22px; font-weight: 700; color: {theme.PRIMARY}; "
            f"font-family: {theme.HEADING_FONT_FAMILY};"
        )
        heading.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        card_layout.addWidget(heading)

        subcopy = QLabel("Ingresá tus credenciales para continuar.")
        subcopy.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 13px;")
        card_layout.addWidget(subcopy)

        card_layout.addSpacing(18)

        username_field, self._username_input = labeled_field("Usuario", "Ej. jperez")
        card_layout.addWidget(username_field)

        card_layout.addSpacing(12)

        password_field, self._password_input = labeled_field("Contraseña")
        self._password_input.setEchoMode(self._password_input.EchoMode.Password)
        card_layout.addWidget(password_field)

        card_layout.addSpacing(20)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # indeterminate spinner
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(4)
        self._progress.hide()
        card_layout.addWidget(self._progress)

        self._submit_button = QPushButton("Ingresar")
        self._submit_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._submit_button.setMinimumHeight(42)
        self._submit_button.setStyleSheet(theme.accent_button_style(padding="12px"))
        self._submit_button.clicked.connect(self._on_submit)
        card_layout.addWidget(self._submit_button)

        self._password_input.returnPressed.connect(self._submit_button.click)
        self._username_input.returnPressed.connect(self._submit_button.click)

        form_layout.addWidget(card)

        self._toast = Toast(self)

    def _on_submit(self) -> None:
        username = self._username_input.text().strip()
        password = self._password_input.text()
        if not username or not password:
            self._toast.show_message("Ingrese usuario y contraseña.")
            return

        self._pending_username = username
        self._set_loading(True)
        self._worker = _LoginWorker(self._client, username, password)
        self._worker.succeeded.connect(self._on_success)
        self._worker.failed.connect(self._on_failure)
        self._worker.finished.connect(lambda: self._set_loading(False))
        self._worker.start()

    def _set_loading(self, loading: bool) -> None:
        self._submit_button.setDisabled(loading)
        self._progress.setVisible(loading)

    def _on_success(self, response) -> None:
        self._password_input.clear()
        self.login_succeeded.emit(
            self._pending_username,
            response.access_token,
            response.role,
            response.expires_in_seconds,
        )

    def _on_failure(self, message: str) -> None:
        self._toast.show_message(message)

    def notify_session_expired(self) -> None:
        """Called by MainWindow when it bounces the user back here because the
        token expired mid-session. Shown for longer than the default toast:
        the operator was working in another view when this fired, so the
        message has to survive the moment it takes them to notice the screen
        changed under them. The password is cleared (never the username) so
        logging back in is one field away."""
        self._password_input.clear()
        self._toast.show_message(SESSION_EXPIRED_MESSAGE, duration_ms=8000)
