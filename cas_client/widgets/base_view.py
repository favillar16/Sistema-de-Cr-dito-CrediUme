from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from cas_client import theme


class BaseView(QWidget):
    """Shared base for content-area widgets: global padding + view title.

    Per ES-003 §4 -- all views should build on this rather than laying out
    padding/titles by hand.
    """

    def __init__(self, title: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        # Plain QWidget subclasses ignore stylesheet `background-color`
        # unless this is set -- callers rely on it to paint their own bg.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(32, 32, 32, 32)
        self._layout.setSpacing(16)

        self._title_label: QLabel | None = None
        if title:
            self.set_title(title)

    @property
    def content_layout(self) -> QVBoxLayout:
        return self._layout

    def set_title(self, title: str) -> None:
        if self._title_label is None:
            self._title_label = QLabel(title)
            self._title_label.setStyleSheet(
                f"font-size: 22px; font-weight: 600; color: {theme.PRIMARY}; "
                f"font-family: {theme.HEADING_FONT_FAMILY};"
            )
            self._layout.insertWidget(0, self._title_label)
        else:
            self._title_label.setText(title)
