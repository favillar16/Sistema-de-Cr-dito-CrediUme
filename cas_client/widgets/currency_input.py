from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from cas_client.widgets.form_input import FormInput


def _digits_only(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


def _format_thousands(digits: str) -> str:
    if not digits:
        return ""
    return f"{int(digits):,}".replace(",", ".")


def _decimal_string_to_digits(value: str) -> str:
    """Truncates a server-side decimal string ("15000000.00") to its whole
    part, same rounding convention as formatting.py's gs() -- Gs has no
    cents in practice. Plain digit-stripping would wrongly merge the
    fractional part into the whole number (e.g. "50000.00" -> "5000000")."""
    if not value:
        return ""
    try:
        amount = Decimal(value)
    except InvalidOperation:
        return _digits_only(value)
    return str(int(amount.to_integral_value(rounding=ROUND_HALF_UP)))


class CurrencyInput(FormInput):
    """FormInput that auto-formats with thousands/millions separator dots
    while typing (Gs has no cents in practice, same convention as
    formatting.py's gs()). raw_value() returns plain digits for the gRPC
    request; set_amount() formats a server-side decimal string ("15000000.00")
    when pre-filling an edit form, without re-triggering the live-typing
    formatter."""

    def __init__(self, placeholder: str = "", parent=None):
        super().__init__(placeholder, parent)
        self.textEdited.connect(self._on_text_edited)

    def _on_text_edited(self, text: str) -> None:
        cursor = self.cursorPosition()
        digits_before_cursor = sum(ch.isdigit() for ch in text[:cursor])
        digits = _digits_only(text)
        formatted = _format_thousands(digits)

        self.blockSignals(True)
        self.setText(formatted)
        self.blockSignals(False)

        pos = len(formatted)
        seen = 0
        for i, ch in enumerate(formatted):
            if ch.isdigit():
                seen += 1
            if seen == digits_before_cursor:
                pos = i + 1
                break
        self.setCursorPosition(pos)

    def raw_value(self) -> str:
        return _digits_only(self.text())

    def set_amount(self, value: str) -> None:
        self.setText(_format_thousands(_decimal_string_to_digits(value)))
