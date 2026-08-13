from PySide6.QtWidgets import QGridLayout, QWidget


class ResponsiveGrid(QWidget):
    """Grid that recomputes its column count from the available width
    instead of a hardcoded column count -- replaces the QGridLayout(...,
    row, col) call sites that used to compress their cells (stat tiles,
    paired form fields) instead of reflowing when the window narrows.

    Widgets are added in order via add_widget() and re-laid-out left to
    right, wrapping to a new row once `min_cell_width` no longer fits.
    """

    def __init__(
        self,
        min_cell_width: int = 260,
        spacing: int = 12,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._min_cell_width = min_cell_width
        self._widgets: list[QWidget] = []
        self._columns = 1
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(spacing)

    def add_widget(self, widget: QWidget) -> None:
        self._widgets.append(widget)
        self._reflow(force=True)

    def clear(self) -> None:
        """Removes and deletes every widget currently in the grid. Callers
        that rebuild their tiles from scratch on each refresh (rather than
        mutating existing labels via setText()) use this first -- see
        dashboard_view.py's note on the stale-repaint bug that motivated
        rebuilding instead of mutating."""
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._widgets = []

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._reflow()

    def _reflow(self, force: bool = False) -> None:
        columns = max(1, self.width() // self._min_cell_width)
        if not force and columns == self._columns:
            return
        self._columns = columns
        while self._layout.count():
            self._layout.takeAt(0)
        for index, widget in enumerate(self._widgets):
            self._layout.addWidget(widget, index // columns, index % columns)
