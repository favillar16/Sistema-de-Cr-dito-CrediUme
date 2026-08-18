from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from cas_client import theme
from cas_client.widgets.form_input import FormInput

"""Shared building blocks for grouping form fields / stats into bordered
white cards -- originally duplicated as private helpers in clients_view.py
and loans_view.py; promoted here once dashboard_view.py needed the same
pattern as a third caller (see CLAUDE.md's note on when to do this)."""


class _ElidingLabel(QLabel):
    """Single-line label that elides its text with "…" to fit its current
    width instead of wrapping -- the full text is still available on hover.

    stat_tile()'s caption used to word-wrap, with a guessed fixed height
    reserved for a possible second line. That was fragile in two ways: (1)
    ResponsiveGrid lays tiles out via QGridLayout, which doesn't reliably
    propagate heightForWidth through the nested QFrame/QVBoxLayout/
    QHBoxLayout chain here, so nothing actually guaranteed the guessed
    height matched what word-wrap would really need; (2) the guess was
    computed from an assumed font, but theme.py's fonts (Comfortaa/Inter)
    aren't bundled and may render as a substitute with different metrics on
    a given machine (see theme.py's own fallback-font caveat) -- so a
    height that was enough in testing could still be too short elsewhere.
    A single line that elides instead of wrapping has a fixed, exactly
    predictable height regardless of caption length, tile width, or which
    font actually got substituted -- there is no wrap calculation left to
    get wrong, so no overlap is structurally possible."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self._full_text = text
        super().setText(text)
        self.setToolTip(text)

    def setText(self, text: str) -> None:
        self._full_text = text
        self.setToolTip(text)
        self._apply_elided_text()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._apply_elided_text()

    def _apply_elided_text(self) -> None:
        elided = self.fontMetrics().elidedText(
            self._full_text, Qt.TextElideMode.ElideRight, self.width()
        )
        super().setText(elided)


def card(*, shadow: bool = True) -> tuple[QFrame, QVBoxLayout]:
    """`shadow=False` skips the QGraphicsDropShadowEffect below -- used by
    stat_tile() (see its own note), since that effect's cached pixmap is
    prone to a well-known Qt/Windows rendering glitch (stale/corrupted
    horizontal bands) when the card's content is repeatedly mutated via
    setText() after the widget is already shown, which is exactly what
    dashboard_view.py's periodic _refresh_stats() does on every visit --
    this was reported as visible light streaks across the dashboard's stat
    tiles. Static, built-once cards (client/loan/user forms) keep the
    shadow, since they aren't repainted this way."""
    frame = QFrame()
    frame.setObjectName("cardFrame")
    frame.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
    frame.setStyleSheet(
        f"#cardFrame {{ background: white; border: 1px solid {theme.BORDER}; "
        "border-radius: 10px; }"
        f"#cardFrame:hover {{ border: 1px solid {theme.PRIMARY}; }}"
    )
    if shadow:
        # Qt can't share one QGraphicsEffect instance across widgets -- each
        # card needs its own, parented to the frame it shadows.
        effect = QGraphicsDropShadowEffect(frame)
        effect.setBlurRadius(12)
        effect.setOffset(0, 2)
        effect.setColor(QColor(*theme.CARD_SHADOW_RGBA))
        frame.setGraphicsEffect(effect)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(10)
    return frame, layout


def section_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setStyleSheet(
        f"color: {theme.TEXT_MUTED}; font-weight: 600; font-size: 12px; "
        f"text-transform: uppercase; border-bottom: 1px solid {theme.BORDER}; "
        "padding-bottom: 4px;"
    )
    return label


def stat_tile(
    title: str,
    *,
    accent_color: str | None = None,
    icon_text: str = "",
    value_text: str = "—",
) -> tuple[QFrame, QLabel]:
    """icon_text (1-2 chars) renders as a small monogram badge in the tile's
    top-right corner, mirroring HeaderBar's avatar-badge convention -- opt-in
    so existing callers without a natural short label (e.g. the amortization
    schedule's stat tiles) are unaffected. No drop shadow (see card()'s
    `shadow` param note) -- stat tile values get updated in place via
    setText() after the tile is already visible, which is what triggers the
    shadow-effect rendering glitch this avoids.

    `value_text` sets the initial value at construction time. Prefer this
    over mutating the returned QLabel's text repeatedly after the tile is
    already visible where practical (e.g. dashboard_view.py rebuilds its
    tiles from scratch on every refresh instead) -- see that module's
    _rebuild_tiles() docstring for why repeated setText() on a long-lived
    tile was found to leave stale rendering artifacts on this app's actual
    Windows target."""
    frame, layout = card(shadow=False)
    if accent_color:
        frame.setStyleSheet(
            frame.styleSheet()
            + f"#cardFrame {{ border-left: 3px solid {accent_color}; }}"
        )
    top_row = QHBoxLayout()
    top_row.setSpacing(8)
    caption = _ElidingLabel(title)
    caption.setStyleSheet(
        f"color: {theme.TEXT_MUTED}; font-size: 11px; font-weight: 600; "
        "text-transform: uppercase;"
    )
    top_row.addWidget(caption, stretch=1)
    if icon_text:
        badge_bg = accent_color or theme.PRIMARY
        # Lime is background-only (see theme.py) -- pair it with navy text,
        # every other accent gets white text per the same doc's contrast note.
        badge_fg = theme.PRIMARY if badge_bg == theme.ACCENT else "white"
        badge = QLabel(icon_text)
        badge.setFixedSize(28, 28)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setStyleSheet(
            f"background-color: {badge_bg}; color: {badge_fg}; border-radius: 14px; "
            "font-size: 12px; font-weight: 700;"
        )
        top_row.addWidget(badge)
        top_row.setAlignment(badge, Qt.AlignmentFlag.AlignTop)
    layout.addLayout(top_row)
    value = QLabel(value_text)
    value.setStyleSheet(f"color: {theme.PRIMARY}; font-size: 20px; font-weight: 700;")
    layout.addWidget(value)
    return frame, value


def labeled_combo(label_text: str) -> tuple[QWidget, QComboBox]:
    """labeled_field()'s counterpart for a picker instead of a text input.

    Promoted from cash_view.py's private `_combo_field()` once loans_view.py's
    detail page became a second caller needing the same caption-above-control
    pairing (same promotion rule this module's own header describes). Applying
    theme.combo_box_style() here is what makes it structural rather than a
    convention: a combo built through this helper cannot be left unstyled.
    """
    wrapper = QWidget()
    layout = QVBoxLayout(wrapper)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    caption = QLabel(label_text)
    caption.setStyleSheet(
        f"color: {theme.TEXT_MUTED}; font-size: 11px; font-weight: 600;"
    )
    layout.addWidget(caption)
    combo = QComboBox()
    combo.setCursor(Qt.CursorShape.PointingHandCursor)
    combo.setStyleSheet(theme.combo_box_style())
    # Sin esto el combo crece hasta el ancho de su ítem más largo y arrastra
    # toda la fila con él -- las etiquetas de cuota ("Cuota 3 · Vence
    # 10/12/2026 · 950.000 Gs") son largas, y era parte de por qué la fila de
    # cobro no entraba en la ventana. Con el texto elidido, el combo se adapta
    # a la celda y el valor completo sigue estando en el desplegable.
    combo.setSizeAdjustPolicy(
        QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
    )
    combo.setMinimumContentsLength(10)
    # El texto elidido queda recuperable al pasar el mouse. Importa en la fila
    # de cobro: la etiqueta de la cuota lleva el vencimiento y el monto, y el
    # operador tiene que poder confirmar cuál eligió sin abrir el desplegable.
    combo.currentIndexChanged.connect(
        lambda _index, c=combo: c.setToolTip(c.currentText())
    )
    layout.addWidget(combo)
    return wrapper, combo


def labeled_field(
    label_text: str,
    placeholder: str = "",
    *,
    required: bool = False,
    input_cls: type[FormInput] = FormInput,
) -> tuple[QWidget, FormInput]:
    """A small caption above a FormInput, as one wrapper widget for grid/card
    layouts. Placeholder text alone (the previous convention) disappears once
    the user types, losing the field's meaning -- this keeps it visible.

    `input_cls` defaults to plain FormInput; pass CurrencyInput for "(Gs)"
    fields that should auto-format with thousands separators while typing.
    """
    wrapper = QWidget()
    layout = QVBoxLayout(wrapper)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    caption = QLabel()
    if required:
        caption.setTextFormat(Qt.TextFormat.RichText)
        caption.setText(f'{label_text} <span style="color:{theme.ERROR};">*</span>')
    else:
        caption.setText(label_text)
    caption.setStyleSheet(
        f"color: {theme.TEXT_MUTED}; font-size: 11px; font-weight: 600;"
    )
    layout.addWidget(caption)
    field = input_cls(placeholder)
    layout.addWidget(field)
    return wrapper, field
