from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QHeaderView, QLabel, QTableWidget

from cas_client import theme


class _EmptyOverlay(QObject):
    """Mantiene un cartel centrado sobre el viewport de una tabla mientras la
    tabla no tenga filas.

    Es un QObject y no una función suelta porque tiene que sobrevivir a
    set_empty_message(): es el filtro de eventos que sigue el redimensionado
    del viewport. Se parenta al viewport, así que Qt lo destruye junto con la
    tabla.

    El cartel es un QLabel hijo del viewport y no un dibujo en `paintEvent`:
    reasignar `viewport.paintEvent` desde Python no reemplaza el virtual de
    C++ (PySide6 sólo despacha a métodos definidos en la clase, no a atributos
    de la instancia), así que ese camino no pinta nada -- se intentó primero y
    quedaba en silencio, sin error.
    """

    def __init__(self, table: QTableWidget, message: str):
        viewport = table.viewport()
        super().__init__(viewport)
        self._table = table
        self._label = QLabel(message, viewport)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setWordWrap(True)
        self._label.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 13px; background: transparent;"
        )
        viewport.installEventFilter(self)
        # El modelo avisa de todo cambio de filas -- así el cartel se
        # sincroniza solo y ninguna vista tiene que acordarse de mostrarlo u
        # ocultarlo en cada refresco.
        modelo = table.model()
        modelo.rowsInserted.connect(self._sync)
        modelo.rowsRemoved.connect(self._sync)
        modelo.modelReset.connect(self._sync)
        self._sync()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize:
            self._sync()
        return False

    def _sync(self, *_args) -> None:
        self._label.setGeometry(self._table.viewport().rect())
        self._label.setVisible(self._table.rowCount() == 0)
        self._label.raise_()


def set_empty_message(table: QTableWidget, message: str) -> None:
    """Muestra `message` centrado sobre la tabla mientras no tenga filas.

    Una tabla vacía se dibujaba como un rectángulo blanco enorme bajo un
    encabezado navy, sin decir nada: el operador no podía distinguir "no hay
    resultados" de "todavía no busqué" ni de "falló la consulta". La lista de
    usuarios, el historial de arqueos y las dos listas de clientes arrancaban
    todas así.

    Se instala una vez, al construir la tabla; a partir de ahí el cartel se
    muestra y se esconde solo según rowCount(), sin que las vistas tengan que
    sincronizar nada en cada refresco (que es lo que cash_view.py sí hace a
    mano con su `_movement_empty` para la tabla de movimientos).
    """
    _EmptyOverlay(table, message)


def size_columns(table: QTableWidget, stretch_column: int) -> None:
    """Every column sizes to its own content, except `stretch_column`, which
    absorbs the leftover width.

    Call sites used to set Stretch on one column and leave the rest at Qt's
    default 100px section width, which silently clipped any header longer than
    that -- "Saldo restante (Gs)" rendered as "Saldo restante (" in the
    amortization schedule, and "Vencimiento"/"Cuota vencida (N)" were tight for
    the same reason. ResizeToContents measures the header text too, not just
    the cells, so a column is never narrower than its own title.
    """
    header = table.horizontalHeader()
    for column in range(table.columnCount()):
        header.setSectionResizeMode(
            column,
            (
                QHeaderView.ResizeMode.Stretch
                if column == stretch_column
                else QHeaderView.ResizeMode.ResizeToContents
            ),
        )


def style_table(table: QTableWidget) -> None:
    """Shared visual treatment for every QTableWidget in the app -- plain Qt
    tables render with bold-but-flat headers, visible grid lines, and
    numbered row headers that add no value here. Applied once per table
    instead of duplicating a stylesheet per call site."""
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(38)
    table.horizontalHeader().setDefaultAlignment(
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
    )
    table.horizontalHeader().setHighlightSections(False)
    table.setStyleSheet(
        f"""
        QTableWidget {{
            background-color: white;
            alternate-background-color: {theme.APP_BACKGROUND};
            gridline-color: transparent;
            border: 1px solid {theme.BORDER};
            border-radius: 8px;
        }}
        QTableWidget::item {{
            padding: 8px 10px;
            border-bottom: 1px solid {theme.APP_BACKGROUND};
        }}
        QTableWidget::item:selected {{
            background-color: {theme.PRIMARY};
            color: white;
        }}
        QHeaderView::section {{
            background-color: {theme.PRIMARY};
            color: white;
            padding: 8px 10px;
            border: none;
            font-weight: 600;
            font-size: 12px;
        }}
        QHeaderView::section:first {{
            border-top-left-radius: 8px;
        }}
        QHeaderView::section:last {{
            border-top-right-radius: 8px;
        }}
        QTableCornerButton::section {{
            background-color: {theme.PRIMARY};
            border: none;
        }}
        """
    )
