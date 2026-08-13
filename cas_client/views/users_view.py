import grpc
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cas_client import theme
from cas_client.grpc_client import AuthClient, AuthError
from cas_client.rbac_ui import can_manage_users
from cas_client.session import Session
from cas_client.widgets.async_worker import AsyncWorker
from cas_client.widgets.base_view import BaseView
from cas_client.widgets.card import card, labeled_field, section_label
from cas_client.widgets.form_input import FormInput
from cas_client.widgets.responsive_grid import ResponsiveGrid
from cas_client.widgets.table import style_table
from cas_client.widgets.toast import Toast

_ROLES = ("CASHIER", "CREDIT_ANALYST", "MANAGER", "ADMIN")
_TABLE_HEADERS = ("Usuario", "Rol", "Estado", "Último acceso", "")
_COL_ACTION = len(_TABLE_HEADERS) - 1


def _friendly_message(exc: Exception) -> str:
    if isinstance(exc, AuthError):
        if exc.code == grpc.StatusCode.ALREADY_EXISTS:
            return "Ya existe un usuario con ese nombre."
        if exc.code == grpc.StatusCode.INVALID_ARGUMENT:
            return f"Datos inválidos: {exc.message}"
        if exc.code == grpc.StatusCode.NOT_FOUND:
            return "Usuario no encontrado."
        if exc.code == grpc.StatusCode.PERMISSION_DENIED:
            return "No tiene permisos para realizar esta acción."
        if exc.code == grpc.StatusCode.UNAVAILABLE:
            return "No se pudo conectar con el servidor."
        return "Ocurrió un error inesperado. Intente nuevamente."
    return f"No se pudo conectar con el servidor: {exc}"


def _combo_style() -> str:
    # Mirrors FormInput's visual language (cas_client/widgets/form_input.py) --
    # no QComboBox existed anywhere in this app before, so there was no
    # existing style to reuse.
    return f"""
        QComboBox {{
            border: 1px solid {theme.BORDER};
            border-radius: 6px;
            padding: 8px 10px;
            font-size: 14px;
            background: #FFFFFF;
            color: {theme.TEXT_PRIMARY};
        }}
        QComboBox:focus {{
            border: 1px solid {theme.PRIMARY};
        }}
    """


class UsersView(BaseView):
    """Alta de usuarios operadores y restablecimiento de contraseña.

    Pantalla exclusiva de Administrador (rbac_ui.can_manage_users) -- el ítem
    "Usuarios" del sidebar está oculto para cualquier otro rol en
    main_window.py, mismo criterio "ocultar en vez de deshabilitar" que el
    resto de las acciones restringidas por rol en este cliente.
    """

    def __init__(
        self, client: AuthClient, session: Session, parent: QWidget | None = None
    ):
        super().__init__("Usuarios", parent=parent)
        self._client = client
        self._session = session
        self._worker: AsyncWorker | None = None

        create_frame, create_layout = card()
        create_layout.addWidget(section_label("Nuevo usuario"))

        grid = ResponsiveGrid(min_cell_width=220)
        username_field, self._new_username = labeled_field("Usuario")
        grid.add_widget(username_field)
        password_field, self._new_password = labeled_field(
            "Contraseña", "Mínimo 8 caracteres"
        )
        self._new_password.setEchoMode(FormInput.EchoMode.Password)
        grid.add_widget(password_field)

        role_wrapper = QWidget()
        role_layout = QVBoxLayout(role_wrapper)
        role_layout.setContentsMargins(0, 0, 0, 0)
        role_layout.setSpacing(4)
        role_caption = QLabel("Rol")
        role_caption.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 11px; font-weight: 600;"
        )
        role_layout.addWidget(role_caption)
        self._new_role = QComboBox()
        self._new_role.addItems(_ROLES)
        self._new_role.setStyleSheet(_combo_style())
        role_layout.addWidget(self._new_role)
        grid.add_widget(role_wrapper)
        create_layout.addWidget(grid)

        create_button = QPushButton("Crear usuario")
        create_button.setCursor(Qt.CursorShape.PointingHandCursor)
        create_button.setStyleSheet(theme.accent_button_style(padding="10px"))
        create_button.clicked.connect(self._on_create_submit)
        create_layout.addWidget(create_button)
        self.content_layout.addWidget(create_frame)

        self.content_layout.addWidget(section_label("Usuarios existentes"))

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(4)
        self._progress.hide()
        self.content_layout.addWidget(self._progress)

        self._error_label = QLabel("")
        self._error_label.setStyleSheet(f"color: {theme.ERROR}; font-size: 12px;")
        self._error_label.setWordWrap(True)
        self._error_label.hide()
        self.content_layout.addWidget(self._error_label)

        self._table = QTableWidget(0, len(_TABLE_HEADERS))
        self._table.setHorizontalHeaderLabels(_TABLE_HEADERS)
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self._table.horizontalHeader().setSectionResizeMode(
            _COL_ACTION, QHeaderView.ResizeMode.ResizeToContents
        )
        style_table(self._table)
        self.content_layout.addWidget(self._table)

        self._toast = Toast(self)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._session.access_token and can_manage_users(self._session.role):
            self._refresh_users()

    def _show_progress(self) -> None:
        self._progress.setRange(0, 0)
        self._progress.show()

    def _hide_progress(self) -> None:
        # Same fix as dashboard_view.py's indeterminate-progress-bar ghost
        # artifact: stop the busy animation while hidden instead of just
        # toggling visibility.
        self._progress.hide()
        self._progress.setRange(0, 1)
        self._progress.setValue(0)

    def _refresh_users(self) -> None:
        self._error_label.hide()
        self._show_progress()
        self._worker = AsyncWorker(
            self._client.list_users,
            self._session.access_token,
            error_translator=_friendly_message,
        )
        self._worker.succeeded.connect(self._on_users_loaded)
        self._worker.failed.connect(self._on_users_failed)
        self._worker.finished.connect(self._hide_progress)
        self._worker.start()

    def _on_users_loaded(self, response) -> None:
        self._table.setRowCount(0)
        for user in response.users:
            row = self._table.rowCount()
            self._table.insertRow(row)
            last_login = (
                user.last_login.ToDatetime().strftime("%Y-%m-%d %H:%M")
                if user.HasField("last_login")
                else "Nunca"
            )
            values = (
                user.username,
                user.role,
                "Bloqueado" if user.is_locked else "Activo",
                last_login,
            )
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, user.id)
                self._table.setItem(row, col, item)

            reset_button = QPushButton("Restablecer contraseña")
            reset_button.setCursor(Qt.CursorShape.PointingHandCursor)
            reset_button.setStyleSheet(theme.flat_button_style())
            reset_button.clicked.connect(
                lambda _checked=False, user_id=user.id, username=user.username: (
                    self._on_reset_password(user_id, username)
                )
            )
            self._table.setCellWidget(row, _COL_ACTION, reset_button)

    def _on_users_failed(self, message: str) -> None:
        self._error_label.setText(message)
        self._error_label.show()

    def _on_create_submit(self) -> None:
        username = self._new_username.text().strip()
        password = self._new_password.text()
        role = self._new_role.currentText()
        if not username or not password:
            self._toast.show_message("Ingrese usuario y contraseña.")
            return
        if len(password) < 8:
            self._toast.show_message("La contraseña debe tener al menos 8 caracteres.")
            return

        self._show_progress()
        self._worker = AsyncWorker(
            self._client.create_user,
            self._session.access_token,
            error_translator=_friendly_message,
            username=username,
            password=password,
            role=role,
        )
        self._worker.succeeded.connect(self._on_create_success)
        self._worker.failed.connect(self._on_error)
        self._worker.finished.connect(self._hide_progress)
        self._worker.start()

    def _on_create_success(self, _response) -> None:
        self._new_username.clear()
        self._new_password.clear()
        self._new_role.setCurrentIndex(0)
        self._toast.show_message("Usuario creado.")
        self._refresh_users()

    def _on_reset_password(self, user_id: str, username: str) -> None:
        password, ok = QInputDialog.getText(
            self,
            "Restablecer contraseña",
            f'Nueva contraseña para "{username}" (mínimo 8 caracteres):',
            echo=QLineEdit.EchoMode.Password,
        )
        if not ok:
            return
        if len(password) < 8:
            self._toast.show_message("La contraseña debe tener al menos 8 caracteres.")
            return

        self._show_progress()
        self._worker = AsyncWorker(
            self._client.reset_password,
            self._session.access_token,
            error_translator=_friendly_message,
            target_user_id=user_id,
            new_password=password,
        )
        self._worker.succeeded.connect(
            lambda _r: self._toast.show_message("Contraseña restablecida.")
        )
        self._worker.failed.connect(self._on_error)
        self._worker.finished.connect(self._hide_progress)
        self._worker.start()

    def _on_error(self, message: str) -> None:
        self._toast.show_message(message)
