from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from cas_client import assets, theme
from cas_client.grpc_client import (
    AuthClient,
    AuthError,
    ClientServiceClient,
    DashboardServiceClient,
    LoanServiceClient,
)
from cas_client.rbac_ui import can_manage_users
from cas_client.session import Session, session_events
from cas_client.views.clients_view import ClientsView
from cas_client.views.dashboard_view import DashboardView
from cas_client.views.loans_view import LoansView
from cas_client.views.login_view import LoginView
from cas_client.views.users_view import UsersView
from cas_client.widgets.header_bar import HeaderBar
from cas_client.widgets.scroll_area import wrap_scrollable

NAV_ITEMS = ("Dashboard", "Clientes", "Préstamos", "Usuarios")


class _Sidebar(QWidget):
    """Persistent left nav per ES-003 §1."""

    item_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(220)
        self.setStyleSheet(f"background-color: {theme.SIDEBAR_BG};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 24, 20, 24)
        layout.setSpacing(4)

        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(0, 0, 0, 0)
        brand_row.setSpacing(8)
        brand_icon = QLabel()
        brand_icon.setPixmap(
            assets.logo_mark_pixmap().scaledToHeight(
                26, Qt.TransformationMode.SmoothTransformation
            )
        )
        brand_row.addWidget(brand_icon)
        brand_text = QLabel(theme.BRAND_NAME)
        # Fixed 220px sidebar (ES-003 §1) leaves ~180px of content width
        # after margins; icon + spacing + the wordmark at 17px didn't fit in
        # that (was getting silently clipped by the layout). 15px used to be
        # the largest size that fit "CrediUME"; "CREDIMED UME" is 4 chars
        # longer *and* all-caps, so it needs 13px to clear the same budget.
        brand_text.setStyleSheet(
            f"font-family: {theme.HEADING_FONT_FAMILY}; "
            "font-size: 13px; font-weight: 700; color: white;"
        )
        # Without this, the QHBoxLayout compresses the label below its own
        # text width whenever the row is tight on space (same surprising
        # QLabel default behavior worked around in login_view.py's wordmark
        # -- see that file's comment for the full explanation), silently
        # clipping the wordmark instead of leaving the icon+text at their
        # natural size and letting the trailing stretch absorb any slack.
        brand_text.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred
        )
        brand_row.addWidget(brand_text)
        brand_row.addStretch()
        brand_container = QWidget()
        brand_container.setLayout(brand_row)
        brand_container.setStyleSheet("margin-bottom: 24px;")
        layout.addWidget(brand_container)

        self._buttons: dict[str, QPushButton] = {}
        for label in NAV_ITEMS:
            button = QPushButton(label)
            button.setFlat(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(
                lambda _checked=False, item=label: self.item_selected.emit(item)
            )
            layout.addWidget(button)
            self._buttons[label] = button

        layout.addStretch()

        self.set_active(NAV_ITEMS[0])

    def set_role(self, role: str) -> None:
        # Hide, don't disable -- same convention already used for other
        # role-gated actions in this client (e.g. the loan schedule's
        # "Ajustar" button) rather than offering an item that would just
        # come back PERMISSION_DENIED.
        self._buttons["Usuarios"].setVisible(can_manage_users(role))

    def set_active(self, label: str) -> None:
        for item, button in self._buttons.items():
            if item == label:
                button.setStyleSheet(
                    f"QPushButton {{ text-align: left; border: none; "
                    f"background-color: {theme.ACCENT}; color: {theme.PRIMARY}; "
                    "font-size: 14px; font-weight: 700; border-radius: 8px; "
                    "padding: 10px 12px; }"
                )
            else:
                button.setStyleSheet(
                    "QPushButton { text-align: left; border: none; "
                    f"background: transparent; color: {theme.ON_DARK_MUTED}; "
                    "font-size: 14px; font-weight: 400; border-radius: 8px; "
                    "padding: 10px 12px; }"
                    "QPushButton:hover { background-color: rgba(255, 255, 255, 20); "
                    "color: white; }"
                )


class _AppShellView(QWidget):
    """Post-login shell: sidebar + content area that swaps Dashboard/Clientes/Préstamos."""

    def __init__(
        self,
        dashboard_view: DashboardView,
        clients_view: ClientsView,
        loans_view: LoansView,
        users_view: UsersView,
        on_logout,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._sidebar = _Sidebar()
        self._sidebar.item_selected.connect(self._on_nav_selected)
        layout.addWidget(self._sidebar)

        self._dashboard_view = dashboard_view
        self._clients_view = clients_view

        self._header_bar = HeaderBar()
        self._header_bar.logout_requested.connect(on_logout)

        self._content_stack = QStackedWidget()
        self._content_stack.setStyleSheet(f"background-color: {theme.APP_BACKGROUND};")
        self._content_stack.addWidget(wrap_scrollable(self._dashboard_view))
        self._content_stack.addWidget(clients_view)
        self._content_stack.addWidget(loans_view)
        self._content_stack.addWidget(wrap_scrollable(users_view))

        content_column = QVBoxLayout()
        content_column.setContentsMargins(0, 0, 0, 0)
        content_column.setSpacing(0)
        content_column.addWidget(self._header_bar)
        content_column.addWidget(self._content_stack, stretch=1)
        layout.addLayout(content_column, stretch=1)

        self._loans_view = loans_view

    def _on_nav_selected(self, label: str) -> None:
        self._sidebar.set_active(label)
        index = {"Dashboard": 0, "Clientes": 1, "Préstamos": 2, "Usuarios": 3}[label]
        self._content_stack.setCurrentIndex(index)

    def show_loans_for_client(self, client_id: str, display_name: str) -> None:
        self._loans_view.load_client(client_id, display_name)
        self._sidebar.set_active("Préstamos")
        self._content_stack.setCurrentIndex(2)

    def show_client_detail(self, client_id: str) -> None:
        self._clients_view.open_client_detail(client_id)
        self._sidebar.set_active("Clientes")
        self._content_stack.setCurrentIndex(1)

    def set_user(self, username: str, role: str) -> None:
        self._dashboard_view.set_user(username, role)
        self._header_bar.set_user(username, role)
        self._sidebar.set_role(role)
        self._sidebar.set_active("Dashboard")
        self._content_stack.setCurrentIndex(0)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{theme.BRAND_NAME} - CAS")
        self.resize(1100, 680)
        # The sidebar stays a fixed 220px (ES-003 §1); below this the content
        # area has too little room left for the reflowed grids to be usable.
        # Also comfortably clears LoginView's own computed minimum width
        # (~843px for its two-panel layout) with headroom, rather than
        # sitting right at that boundary.
        self.setMinimumSize(900, 560)

        self._auth_client = AuthClient()
        self._session = Session()

        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        self._login_view = LoginView(self._auth_client)
        self._login_view.login_succeeded.connect(self._on_login_succeeded)

        client_service = ClientServiceClient()
        clients_view = ClientsView(client_service, self._session)
        clients_view.view_loans_requested.connect(self._on_view_loans_requested)
        loans_view = LoansView(LoanServiceClient(), client_service, self._session)
        loans_view.view_client_requested.connect(self._on_view_client_requested)
        dashboard_view = DashboardView(DashboardServiceClient(), self._session)
        users_view = UsersView(self._auth_client, self._session)

        self._shell_view = _AppShellView(
            dashboard_view,
            clients_view,
            loans_view,
            users_view,
            on_logout=self._on_logout_clicked,
        )

        self._stack.addWidget(self._login_view)
        self._stack.addWidget(self._shell_view)
        self._stack.setCurrentWidget(self._login_view)

        session_events.expired.connect(self._on_session_expired)

    def _on_login_succeeded(
        self, username: str, token: str, role: str, expires_in: int
    ) -> None:
        self._session.access_token = token
        self._session.role = role
        self._session.username = username
        self._shell_view.set_user(username, role)
        self._stack.setCurrentWidget(self._shell_view)

    def _on_view_loans_requested(self, client_id: str, display_name: str) -> None:
        self._shell_view.show_loans_for_client(client_id, display_name)

    def _on_view_client_requested(self, client_id: str) -> None:
        self._shell_view.show_client_detail(client_id)

    def _on_logout_clicked(self) -> None:
        if self._session.access_token:
            try:
                self._auth_client.logout(self._session.access_token)
            except AuthError:
                pass  # best-effort server-side revoke; still log out locally
        self._session.clear()
        self._shell_view.set_user("", "")
        self._stack.setCurrentWidget(self._login_view)

    def _on_session_expired(self) -> None:
        """BR-AUTH-003's 8-hour token ran out (or was revoked) while the app
        was open. Unlike _on_logout_clicked this does NOT call Logout: the
        token the server just rejected is exactly the one we'd be sending, so
        the round trip can only fail. Guarded on access_token because several
        in-flight calls can fail at once (e.g. a dashboard refresh alongside a
        list query) and each emits `expired` -- only the first one should
        actually tear the session down."""
        if not self._session.access_token:
            return
        self._session.clear()
        self._shell_view.set_user("", "")
        self._stack.setCurrentWidget(self._login_view)
        self._login_view.notify_session_expired()
