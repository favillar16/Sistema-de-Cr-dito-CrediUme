from PySide6.QtWidgets import QLineEdit, QWidget

from cas_client import theme


class FormInput(QLineEdit):
    """QLineEdit subclass per ES-003 §4: rounded border, >=8px internal padding."""

    def __init__(self, placeholder: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        if placeholder:
            self.setPlaceholderText(placeholder)
        self.setStyleSheet(theme.line_edit_style())

    def set_error(self, has_error: bool) -> None:
        self.setProperty("error", has_error)
        # Qt caches computed style -- without this the dynamic-property
        # selector above never gets re-evaluated after the initial polish.
        self.style().unpolish(self)
        self.style().polish(self)
