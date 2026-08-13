from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


def gs(value: str) -> str:
    """Formats a decimal-string monetary amount as a Guaraní (PYG) amount --
    the only currency this system handles (Paraguay), which has no
    minor/cents subdivision in practice: "1000000.00" -> "1.000.000 Gs".
    Display-only: never apply this to text read back from an editable
    FormInput, since the server expects a plain decimal string
    (analizar_decimal)."""
    if not value:
        return value
    try:
        amount = Decimal(value)
    except InvalidOperation:
        return value
    whole = int(amount.to_integral_value(rounding=ROUND_HALF_UP))
    return f"{whole:,}".replace(",", ".") + " Gs"


def rate_percent(value: str) -> str:
    """Formats a decimal-fraction interest rate as a plain percentage for
    display: "0.24" -> "24%". The server stores/returns interest_rate as the
    raw decimal fraction (analizar_decimal) -- this is display-only, same
    contract as gs() above; never feed the result back into an editable
    field expecting the server's decimal-fraction format."""
    if not value:
        return value
    try:
        percent = Decimal(value) * 100
    except InvalidOperation:
        return value
    text = f"{percent:f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return f"{text}%"
