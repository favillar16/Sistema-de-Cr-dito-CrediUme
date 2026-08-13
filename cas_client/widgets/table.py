from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidget

from cas_client import theme


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
