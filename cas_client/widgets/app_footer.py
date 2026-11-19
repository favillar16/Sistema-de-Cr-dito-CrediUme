"""Pie de página de la ventana: el sistema a la izquierda y, a la derecha, la
empresa que lo desarrolló (Angiru Tech).

Va en MainWindow, debajo de TODO el stack (login incluido), y no dentro de
una vista: es la firma del producto, no contenido de una pantalla. Es sólo
de la ventana -- los documentos impresos (Pagaré, Contrato, comprobantes) no
lo llevan, por decisión de la entidad: son papeles suyos, no del proveedor.

Los datos salen de la carpeta de marca de Angiru Tech (publicacion.md): el
nombre, el ícono oficial (assets.ANGIRU_ICON_PNG) y el número de contacto
que la propia marca publica para atención.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

from cas_client import assets, theme

DEVELOPER_NAME = "Angiru Tech"
DEVELOPER_CONTACT = "0984 992 634"
SYSTEM_NAME = "Sistema de Administración de Créditos"


class AppFooter(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("appFooter")
        self.setFixedHeight(30)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"#appFooter {{ background: #FFFFFF; "
            f"border-top: 1px solid {theme.DIVIDER}; }}"
            f"QLabel {{ background: transparent; color: {theme.TEXT_SECONDARY}; "
            "font-size: 11px; }"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 16, 0)
        layout.setSpacing(6)

        self.system_label = QLabel(f"{theme.BRAND_NAME} · {SYSTEM_NAME}")
        layout.addWidget(self.system_label)
        layout.addStretch()

        layout.addWidget(QLabel("Desarrollado por"))
        icon = QLabel()
        icon.setPixmap(
            QPixmap(assets.ANGIRU_ICON_PNG).scaledToHeight(
                16, Qt.TransformationMode.SmoothTransformation
            )
        )
        layout.addWidget(icon)
        self.developer_label = QLabel(DEVELOPER_NAME)
        self.developer_label.setStyleSheet(
            f"color: {theme.DEVELOPER_BRAND_COLOR}; font-weight: 700; "
            "font-size: 11px;"
        )
        layout.addWidget(self.developer_label)
        layout.addWidget(QLabel(f"· {DEVELOPER_CONTACT}"))
        self.setToolTip(
            f"{DEVELOPER_NAME} — desarrollo de software. Contacto: {DEVELOPER_CONTACT}"
        )
