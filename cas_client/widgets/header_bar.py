from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from cas_client import theme
from cas_client.rbac_ui import tier_label


def _initials(username: str) -> str:
    parts = username.split()
    if len(parts) >= 2:
        return (parts[0][0] + parts[1][0]).upper()
    return username[:1].upper() if username else ""


class HeaderBar(QWidget):
    """Persistent top bar above the content stack in _AppShellView.

    Rediseño 2026-11: blanca en vez de navy. Con el menú lateral también en
    navy, la pantalla tenía dos bloques oscuros compitiendo y el contenido
    quedaba encajonado. Además ahora dice **dónde está el operador y para qué
    sirve esa pantalla** (`set_section`): el título de la sección y una línea
    de ayuda, que antes no existía en ninguna parte. Por eso las vistas ya no
    repiten su propio título.
    """

    logout_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("headerBar")
        self.setFixedHeight(68)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"#headerBar {{ background: #FFFFFF; "
            f"border-bottom: 1px solid {theme.DIVIDER}; }}"
            "QLabel { background: transparent; }"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(32, 0, 24, 0)
        layout.setSpacing(14)

        # -- Izquierda: sección actual + ayuda de una línea --
        section_column = QVBoxLayout()
        section_column.setContentsMargins(0, 0, 0, 0)
        section_column.setSpacing(0)
        section_column.addStretch()
        self._section_label = QLabel("")
        self._section_label.setStyleSheet(
            f"color: {theme.PRIMARY}; font-size: 18px; font-weight: 700; "
            f"font-family: {theme.HEADING_FONT_FAMILY};"
        )
        section_column.addWidget(self._section_label)
        self._section_help = QLabel("")
        self._section_help.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-size: 12px;"
        )
        # Se puede achicar (y recortar) antes que empujar al bloque del
        # usuario fuera de la barra en la ventana mínima de 900 px.
        self._section_help.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        section_column.addWidget(self._section_help)
        section_column.addStretch()
        layout.addLayout(section_column, stretch=1)

        # Indicador de enlace con el servidor. Va acá, en la barra que está
        # visible en todas las pantallas, porque el operador tiene que poder
        # notar el corte *antes* de intentar cobrar -- no enterarse por un
        # error después de haber tomado la plata en el mostrador.
        self._connection_label = QLabel("")
        self._connection_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Centrado vertical y no estirado: sin esto el QLabel crece hasta el
        # alto del header y el estado de alarma se dibuja como un bloque rojo
        # macizo en vez de una píldora.
        layout.addWidget(
            self._connection_label, alignment=Qt.AlignmentFlag.AlignVCenter
        )
        self.set_connected(True)

        # -- Derecha: usuario (avatar + nombre + nivel) --
        self._avatar = QLabel("")
        self._avatar.setFixedSize(36, 36)
        self._avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._avatar.setStyleSheet(
            f"background-color: {theme.PRIMARY}; color: white; "
            "border-radius: 18px; font-weight: 700; font-size: 13px;"
        )
        layout.addWidget(self._avatar, alignment=Qt.AlignmentFlag.AlignVCenter)

        user_column = QVBoxLayout()
        user_column.setContentsMargins(0, 0, 0, 0)
        user_column.setSpacing(0)
        self._username_label = QLabel("")
        self._username_label.setStyleSheet(
            f"color: {theme.TEXT_PRIMARY}; font-size: 13px; font-weight: 700;"
        )
        user_column.addWidget(self._username_label)
        self._role_badge = QLabel("")
        self._role_badge.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-size: 11px; font-weight: 600;"
        )
        user_column.addWidget(self._role_badge)
        user_wrapper = QWidget()
        user_wrapper.setLayout(user_column)
        user_wrapper.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred
        )
        layout.addWidget(user_wrapper, alignment=Qt.AlignmentFlag.AlignVCenter)

        logout_button = QPushButton("Cerrar sesión")
        logout_button.setCursor(Qt.CursorShape.PointingHandCursor)
        logout_button.setStyleSheet(theme.secondary_button_style(padding="6px 14px"))
        logout_button.clicked.connect(self.logout_requested.emit)
        layout.addWidget(logout_button, alignment=Qt.AlignmentFlag.AlignVCenter)

    def set_section(self, title: str, help_text: str) -> None:
        """Título de la pantalla visible y una línea que dice para qué sirve."""
        self._section_label.setText(title)
        self._section_help.setText(help_text)
        self._section_help.setToolTip(help_text)

    def set_connected(self, connected: bool) -> None:
        """Píldora verde discreta cuando hay línea, roja cuando no.

        El estado normal no debe competir por atención -- es el de siempre --
        así que "Conectado" va en un verde suave. La caída se pinta en rojo
        lleno: es una condición que obliga a parar lo que se esté haciendo.
        """
        if connected:
            self._connection_label.setText("● Conectado")
            self._connection_label.setStyleSheet(
                f"background-color: {theme.SUCCESS_TINT}; color: {theme.SUCCESS}; "
                "border-radius: 11px; font-size: 11px; font-weight: 700; "
                "padding: 4px 10px;"
            )
            self._connection_label.setToolTip("Hay conexión con el servidor.")
            return
        self._connection_label.setText("● Sin conexión")
        self._connection_label.setStyleSheet(
            f"background-color: {theme.ERROR}; color: white; border-radius: 11px; "
            "font-size: 11px; font-weight: 700; padding: 4px 10px;"
        )
        self._connection_label.setToolTip(
            "Se perdió el enlace con el servidor. Las operaciones no se van a "
            "registrar hasta que se restablezca."
        )

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
        self._role_badge.show()
