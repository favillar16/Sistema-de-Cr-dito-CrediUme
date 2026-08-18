"""Shared color palette per docs/Detalles de UI para CrediUme.txt (the "CrediMed"
palette doc) -- supersedes the earlier ES-003 palette by explicit product decision.
Only colors/fonts came from that doc; the brand name below is the entity's real
registered name.

BRAND_NAME is the single source of truth for the establishment's name in the
UI chrome (login wordmark, sidebar, window title). The generated documents
keep their own copy in documents.py's _COMPANY_* block, since those also carry
RUC/address/phone and are a legal-identity concern rather than a styling one --
keep the two in sync by hand if the registered name ever changes again.
"""

# Nombre del establecimiento según su inscripción ante la DNIT (reemplaza al
# anterior "CrediUME" de ES-003, por decisión de producto).
BRAND_NAME = "CREDIMED UME"

APP_BACKGROUND = "#F8F9FA"  # off-white, both docs agree
PRIMARY = "#2B407B"  # navy -- primary/header color
SIDEBAR_BG = "#2B407B"  # navy sidebar (was #1A252F)
ACCENT = "#8CC63F"  # lime -- background-only accent, see note below
ACCENT_HOVER = "#7AB32E"  # lime, ~10% darker -- accent_button_style() :hover
ACCENT_PRESSED = "#6B9E27"  # lime, darker still -- accent_button_style() :pressed
SUCCESS = "#2E9344"  # emerald -- semantic success (approved/paid badges)
TEXT_PRIMARY = "#212121"
TEXT_MUTED = "#808285"  # gray (was #5A6774)
BORDER = "#808285"  # gray dividers
ERROR = "#C0392B"

# TEXT_MUTED (#808285) is calibrated for light backgrounds -- too low-
# contrast against the navy sidebar/login panel. Rather than a separate
# invented gray-blue hex for "muted text on a dark/navy surface", this
# reuses white at reduced alpha wherever that's needed (sidebar inactive
# nav items, login panel subcopy).
ON_DARK_MUTED = "rgba(255, 255, 255, 0.72)"

# QSS has no box-shadow -- QGraphicsDropShadowEffect needs a QColor, not a hex
# string, so this stays a plain RGBA tuple for widgets/card.py to unpack.
# Tinted with PRIMARY (navy, 43/64/123) rather than flat black, at a low
# enough alpha to read as a soft lift instead of a hard drop shadow -- a
# on-brand, lighter alternative to the previous (0, 0, 0, 40).
CARD_SHADOW_RGBA = (43, 64, 123, 22)

# Shared (bg, fg) pair per LoanStatusEnum value -- single source of truth for
# loans_view.py's status badges and dashboard_view.py's per-status stat tiles.
# APPROVED uses navy-on-lime (the doc's own validated 5.2:1 combo) rather than
# white-on-lime, which is illegible.
LOAN_STATUS_COLORS = {
    "PENDING": (TEXT_MUTED, "white"),
    "APPROVED": (ACCENT, PRIMARY),
    "ACTIVE": (PRIMARY, "white"),
    "PAID": (SUCCESS, "white"),
    "DEFAULTED": (ERROR, "white"),
    "EXPIRED": (TEXT_MUTED, "white"),
}

# Per the doc's own WCAG note, lime (#8CC63F) on white is <2:1 contrast --
# illegible. ACCENT is therefore a *background-only* color: pair it with
# TEXT_PRIMARY (navy-ish dark text) on top, never white/light text on lime.

HEADING_FONT_FAMILY = '"Comfortaa", "Segoe UI", sans-serif'
BODY_FONT_FAMILY = '"Inter", "Segoe UI", sans-serif'
# Comfortaa/Inter aren't bundled with the app or guaranteed installed on the
# bare-metal Windows deployment target (docs/ES-004) -- these are fallback
# lists, not a guarantee. Qt falls back to the next name if one isn't present.


def flat_button_style() -> str:
    """Shared stylesheet for flat/borderless nav buttons (e.g. "← Volver").
    Qt's default flat QPushButton palette can resolve to white text on some
    Windows dark-mode setups -- invisible against this app's white
    backgrounds -- so this pins the text color explicitly."""
    return (
        f"QPushButton {{ color: {PRIMARY}; border: none; background: transparent; "
        "font-weight: 600; padding: 4px; }"
    )


def secondary_button_style(*, padding: str = "8px 14px") -> str:
    """Shared stylesheet for standard action buttons (Aprobar, Desembolsar,
    Buscar, etc.) -- native Windows button chrome renders nearly borderless
    here (see CLAUDE.md), which made these blend into the white card
    backgrounds they sit on. Gives every plain button a visible face,
    consistent hover/pressed feedback, and a readable disabled state."""
    return (
        "QPushButton { "
        f"background-color: {APP_BACKGROUND}; color: {TEXT_PRIMARY}; "
        f"border: 1px solid {BORDER}; border-radius: 6px; "
        f"padding: {padding}; font-weight: 600; "
        f"font-family: {BODY_FONT_FAMILY}; "
        "}"
        f"QPushButton:hover {{ background-color: white; border: 1px solid {PRIMARY}; "
        f"color: {PRIMARY}; }}"
        f"QPushButton:pressed {{ background-color: {BORDER}; color: white; }}"
        f"QPushButton:disabled {{ color: {TEXT_MUTED}; background-color: {APP_BACKGROUND}; "
        f"border: 1px solid {BORDER}; }}"
    )


def accent_button_style(*, padding: str = "8px 14px") -> str:
    """Shared stylesheet for primary/CTA buttons -- navy text on lime background
    (5.2:1 contrast, WCAG AA), per the palette doc's contrast recommendations."""
    return (
        "QPushButton { "
        f"background-color: {ACCENT}; color: {PRIMARY}; border-radius: 6px; "
        f"padding: {padding}; font-weight: 600; border: none; "
        f"font-family: {BODY_FONT_FAMILY}; "
        "}"
        f"QPushButton:hover {{ background-color: {ACCENT_HOVER}; }}"
        f"QPushButton:pressed {{ background-color: {ACCENT_PRESSED}; }}"
    )


def combo_box_style() -> str:
    """Shared stylesheet for every QComboBox in the app.

    Promoted out of users_view.py's private `_combo_style()` once loans_view
    and cash_view turned out to be building bare, unstyled `QComboBox()`
    widgets -- the "Cuota a pagar" / "Medio de pago" pickers on the loan
    detail page and the three selectors on the caja's collection card. An
    unstyled combo takes the *system* palette rather than this app's, which is
    the same trap `flat_button_style()` documents: on a Windows machine set to
    dark mode it renders light text on a dark field, sitting inside one of
    this app's white cards. `main.py` disables Qt's dark adaptation globally,
    so those combos are legible today -- but they were relying on that one
    env-var workaround rather than on any styling of their own, which is a
    thin thread for the most-used control on the cashier's screen.

    Pinning the colors here also makes the drop-down list itself match: an
    unstyled QAbstractItemView popup is the other half a user actually reads.
    """
    return (
        "QComboBox { "
        f"border: 1px solid {BORDER}; border-radius: 6px; padding: 8px 10px; "
        f"font-size: 14px; background: #FFFFFF; color: {TEXT_PRIMARY}; "
        f"font-family: {BODY_FONT_FAMILY}; "
        "}"
        f"QComboBox:focus {{ border: 1px solid {PRIMARY}; }}"
        f"QComboBox:hover {{ border: 1px solid {PRIMARY}; }}"
        f"QComboBox:disabled {{ background: {APP_BACKGROUND}; color: {TEXT_MUTED}; }}"
        "QComboBox::drop-down { border: none; width: 22px; }"
        # La lista desplegable es un widget aparte y no hereda lo de arriba --
        # sin esto queda con la paleta del sistema aunque el campo cerrado se
        # vea bien, que es justo el caso que confunde al usuario.
        "QComboBox QAbstractItemView { "
        f"background: #FFFFFF; color: {TEXT_PRIMARY}; "
        f"border: 1px solid {BORDER}; outline: none; "
        f"selection-background-color: {PRIMARY}; selection-color: white; "
        "}"
    )


def danger_button_style(*, padding: str = "8px 14px") -> str:
    """Shared stylesheet for destructive actions (hoy solo "Eliminar
    préstamo", BR-LOAN-012). Deliberadamente NO es un tercer botón lleno de
    color como accent_button_style(): un borde y un texto rojos sobre el mismo
    fondo claro de secondary_button_style() lo distinguen sin competir con la
    llamada a la acción principal de la pantalla, que es lo que el usuario
    debería estar apretando casi siempre. Se rellena en rojo solo al pasar por
    encima, cuando ya es una intención y no un accidente."""
    return (
        "QPushButton { "
        f"background-color: {APP_BACKGROUND}; color: {ERROR}; "
        f"border: 1px solid {ERROR}; border-radius: 6px; "
        f"padding: {padding}; font-weight: 600; "
        f"font-family: {BODY_FONT_FAMILY}; "
        "}"
        f"QPushButton:hover {{ background-color: {ERROR}; color: white; }}"
        f"QPushButton:pressed {{ background-color: #962D21; color: white; }}"
        f"QPushButton:disabled {{ color: {TEXT_MUTED}; background-color: {APP_BACKGROUND}; "
        f"border: 1px solid {BORDER}; }}"
    )
