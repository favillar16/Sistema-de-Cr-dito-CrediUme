from PySide6.QtCore import Qt
from PySide6.QtWidgets import QScrollArea, QWidget


def wrap_scrollable(content: QWidget) -> QScrollArea:
    """Wrap a page widget in a borderless vertical-only QScrollArea.

    Pages built from a plain QVBoxLayout with no scroll container get
    force-compressed by QStackedWidget/BaseView to the visible viewport
    height once they have enough fields (each QLineEdit shrinks below its
    padded sizeHint, so labels/placeholders overlap the border) -- this is
    the fix for that, not a generic scroll-everything wrapper. Use it for
    any create/detail page long enough to exceed the window height.
    """
    scroll = QScrollArea()
    scroll.setWidget(content)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
    content.setStyleSheet(content.styleSheet() + "background: transparent;")
    return scroll
