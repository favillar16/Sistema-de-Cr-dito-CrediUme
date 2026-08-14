from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# Formato de fecha que ve el usuario en toda la app (campos de formulario,
# tablas y documentos generados). El contrato gRPC sigue siendo ISO
# (YYYY-MM-DD, ver los comentarios de los .proto y analizar_fecha() del
# servidor): la traducción pasa únicamente por este módulo, en el borde de la
# UI -- nunca se manda DD/MM/AAAA al servidor ni se muestra ISO al usuario.
DISPLAY_DATE_FORMAT = "%d/%m/%Y"
DISPLAY_DATE_PLACEHOLDER = "DD/MM/AAAA"


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


def fecha(value: str) -> str:
    """Formats a wire-format date string for display: "2026-10-10" ->
    "10/10/2026".

    Display-only, same contract as gs()/rate_percent() above: never feed the
    result back into a request field. An empty or unparseable value is
    returned unchanged rather than raising -- these render into read-only
    labels and table cells where a hard failure would take down the whole
    row for what is only a formatting concern.
    """
    if not value:
        return value
    try:
        return date.fromisoformat(value).strftime(DISPLAY_DATE_FORMAT)
    except (ValueError, TypeError):
        return value


def fecha_a_iso(value: str) -> str:
    """Inverse of fecha(): turns what the user typed into the wire format the
    server's analizar_fecha() expects. "10/10/2026" -> "2026-10-10".

    Already-ISO input passes through unchanged, so a value round-tripped from
    a response that fecha() could not parse is not corrupted here. Anything
    else is returned unchanged too, on purpose: the server is the authority on
    date validity (it aborts INVALID_ARGUMENT with a message the views
    already translate), so silently "fixing" or blanking a malformed date
    here would only hide the real error from the user.
    """
    text = (value or "").strip()
    if not text:
        return text
    for pattern in (DISPLAY_DATE_FORMAT, "%Y-%m-%d"):
        try:
            return datetime.strptime(text, pattern).date().isoformat()
        except ValueError:
            continue
    return text


def fecha_hora(value: datetime) -> str:
    """Same display format as fecha(), plus the 24h clock time -- for the
    datetime columns (e.g. último acceso in users_view)."""
    return value.strftime(f"{DISPLAY_DATE_FORMAT} %H:%M")
