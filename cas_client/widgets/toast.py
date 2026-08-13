from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel, QWidget

from cas_client import theme


class Toast(QLabel):
    """Floating error/info banner.

    Per ES-003 §5: gRPC error codes must be translated into friendly
    messages and shown here -- never as a raw traceback.
    """

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setStyleSheet(
            f"""
            QLabel {{
                background-color: {theme.ERROR};
                color: white;
                border-radius: 6px;
                padding: 10px 16px;
                font-size: 13px;
            }}
            """
        )
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hide()

    def show_message(self, message: str, duration_ms: int = 3500) -> None:
        self.setText(message)
        self.adjustSize()
        parent_rect = self.parentWidget().rect()
        x = (parent_rect.width() - self.width()) // 2
        y = parent_rect.height() - self.height() - 24
        self.move(max(x, 0), max(y, 0))
        self.show()
        self.raise_()
        QTimer.singleShot(duration_ms, self.hide)
