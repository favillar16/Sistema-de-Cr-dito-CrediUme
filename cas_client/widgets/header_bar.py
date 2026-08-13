from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from cas_client import theme
from cas_client.rbac_ui import tier_label


def _initials(username: str) -> str:
    parts = username.split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return username[:1].upper() if username else ""


class HeaderBar(QWidget):
    """Persistent top bar showing who's logged in + logout -- lives above the
    content stack in _AppShellView, visible across Dashboard/Clientes/Préstamos
    instead of being duplicated per view or buried in the sidebar."""

    logout_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedHeight(64)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Navy fill (same as the sidebar) instead of the old plain-white bar --
        # gives the header actual visual weight instead of blending into the
        # content area above it.
        self.setStyleSheet(f"background: {theme.PRIMARY};")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(24, 0, 24, 0)
        layout.setSpacing(12)

        self._avatar = QLabel("")
        self._avatar.setFixedSize(36, 36)
        self._avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Lime-on-navy (ACCENT is background-only per theme.py's contrast
        # rule, always paired with PRIMARY text) -- pops against the new navy
        # header, whereas the old navy-on-navy-adjacent avatar would blend in.
        self._avatar.setStyleSheet(
            f"background-color: {theme.ACCENT}; color: {theme.PRIMARY}; "
            "border-radius: 18px; font-weight: 700; font-size: 13px;"
        )
        layout.addWidget(self._avatar)

        text_column = QVBoxLayout()
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(2)
        self._username_label = QLabel("")
        self._username_label.setStyleSheet(
            "color: white; font-size: 14px; font-weight: 600;"
        )
        text_column.addWidget(self._username_label)

        self._role_badge = QLabel("")
        self._role_badge.setStyleSheet(
            f"background-color: {theme.ACCENT}; color: {theme.PRIMARY}; "
            "border-radius: 10px; padding: 2px 10px; font-size: 11px; "
            "font-weight: 600;"
        )
        self._role_badge.setFixedWidth(self._role_badge.sizeHint().width())
        text_column.addWidget(self._role_badge, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addLayout(text_column)

        layout.addStretch()

        logout_button = QPushButton("Cerrar sesión")
        logout_button.setCursor(Qt.CursorShape.PointingHandCursor)
        logout_button.setStyleSheet(
            "QPushButton { color: white; border: 1px solid rgba(255, 255, 255, 90); "
            "background: transparent; border-radius: 14px; font-weight: 600; "
            "padding: 6px 16px; }"
            f"QPushButton:hover {{ background-color: {theme.ACCENT}; "
            f"color: {theme.PRIMARY}; border-color: {theme.ACCENT}; }}"
        )
        logout_button.clicked.connect(self.logout_requested.emit)
        layout.addWidget(logout_button)

    def set_user(self, username: str, role: str) -> None:
        if not username:
            self._avatar.setText("")
            self._username_label.setText("")
            self._role_badge.setText("")
            self._role_badge.hide()
            return
        self._avatar.setText(_initials(username))
        self._username_label.setText(username)
        self._role_badge.setText(tier_label(role))
        self._role_badge.setFixedWidth(self._role_badge.sizeHint().width())
        self._role_badge.show()
