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

# ---- Rediseño 2026-11 (UI más liviana) -------------------------------------
# El gris de la paleta (#808285) como borde de *cada* tarjeta, campo y tabla
# era lo que más envejecía la interfaz: líneas pesadas en todas partes y nada
# que guiara la vista. Estos tokens son sólo para el cromo de la UI -- BORDER
# y APP_BACKGROUND no cambian porque también los usan los documentos
# impresos (documents.py/documents_docx.py), que no se tocan.
DIVIDER = "#E2E6EE"  # bordes de tarjetas, tablas y separadores
INPUT_BORDER = "#C5CDDC"  # borde de campos y botones secundarios: visible, no pesado
INPUT_BORDER_HOVER = "#8E9AB5"
PRIMARY_TINT = "#EEF2FA"  # navy muy claro: hover, encabezado de tabla
PRIMARY_TINT_STRONG = "#DCE4F4"  # navy claro: fila seleccionada, pressed
SUCCESS_TINT = "#E6F4EA"  # fondo de la píldora "Conectado"
ERROR_TINT = "#FBEAEA"
TEXT_SECONDARY = "#5B6474"  # texto de apoyo con mejor contraste que TEXT_MUTED
RADIUS = 8  # botones y campos
CARD_RADIUS = 12

# Marino de la marca de Angiru Tech (la empresa que desarrolló el sistema),
# sólo para su nombre en el pie de página (widgets/app_footer.py).
DEVELOPER_BRAND_COLOR = "#003049"

# Íconos del menú lateral. "Segoe MDL2 Assets" viene con Windows 10 y 11 (el
# único destino de despliegue, docs/ES-004), así que no hay que empaquetar
# nada; va *después* de la fuente del texto en la lista para que Qt sólo la
# use para los glifos que la otra no tiene.
ICON_FONT_FAMILY = '"Segoe MDL2 Assets"'

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


def tooltip_style() -> str:
    """Stylesheet for every QToolTip in the app.

    Los tooltips salían como un recuadro negro sin texto legible: `QToolTip`
    es la única clase de widget que nunca recibió un `color:`/`background`
    explícito, así que cae en la paleta del sistema (la trampa que documenta
    CLAUDE.md). `QT_QPA_PLATFORM=windows:darkmode=0` de main.py no alcanza --
    desactiva la adaptación automática de los widgets comunes, no la del
    popup del tooltip, que es una ventana top-level aparte.

    Por eso mismo esto **tiene que ir en la hoja de estilos de la aplicación**
    (main.py) y no en la de un widget: el tooltip no es hijo del botón que lo
    dispara, así que un `setStyleSheet` sobre el botón nunca lo alcanza.
    """
    return (
        "QToolTip { "
        f"background-color: #FFFFFF; color: {TEXT_PRIMARY}; "
        f"border: 1px solid {INPUT_BORDER}; border-radius: 6px; "
        f"padding: 6px 8px; font-family: {BODY_FONT_FAMILY}; "
        "}"
    )


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
        f"background-color: #FFFFFF; color: {PRIMARY}; "
        f"border: 1px solid {INPUT_BORDER}; border-radius: {RADIUS}px; "
        f"padding: {padding}; font-weight: 600; "
        f"font-family: {BODY_FONT_FAMILY}; "
        "}"
        f"QPushButton:hover {{ background-color: {PRIMARY_TINT}; "
        f"border: 1px solid {PRIMARY}; color: {PRIMARY}; }}"
        f"QPushButton:pressed {{ background-color: {PRIMARY_TINT_STRONG}; }}"
        f"QPushButton:disabled {{ color: {TEXT_MUTED}; background-color: {APP_BACKGROUND}; "
        f"border: 1px solid {DIVIDER}; }}"
    )


def accent_button_style(*, padding: str = "8px 14px") -> str:
    """Shared stylesheet for primary/CTA buttons -- navy text on lime background
    (5.2:1 contrast, WCAG AA), per the palette doc's contrast recommendations."""
    return (
        "QPushButton { "
        f"background-color: {ACCENT}; color: {PRIMARY}; border-radius: {RADIUS}px; "
        f"padding: {padding}; font-weight: 700; border: 1px solid {ACCENT}; "
        f"font-family: {BODY_FONT_FAMILY}; "
        "}"
        f"QPushButton:hover {{ background-color: {ACCENT_HOVER}; "
        f"border-color: {ACCENT_HOVER}; }}"
        f"QPushButton:pressed {{ background-color: {ACCENT_PRESSED}; }}"
        # Sin esto el botón principal deshabilitado seguía en lima, igual que
        # habilitado: el operador no veía por qué "Cobrar" no respondía.
        f"QPushButton:disabled {{ background-color: {DIVIDER}; color: {TEXT_MUTED}; "
        f"border-color: {DIVIDER}; }}"
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
        f"border: 1px solid {INPUT_BORDER}; border-radius: {RADIUS}px; "
        "padding: 8px 10px; "
        f"font-size: 14px; background: #FFFFFF; color: {TEXT_PRIMARY}; "
        f"font-family: {BODY_FONT_FAMILY}; "
        "}"
        f"QComboBox:hover {{ border: 1px solid {INPUT_BORDER_HOVER}; }}"
        f"QComboBox:focus {{ border: 1px solid {PRIMARY}; }}"
        f"QComboBox:disabled {{ background: {APP_BACKGROUND}; color: {TEXT_MUTED}; }}"
        "QComboBox::drop-down { border: none; width: 22px; }"
        # La lista desplegable es un widget aparte y no hereda lo de arriba --
        # sin esto queda con la paleta del sistema aunque el campo cerrado se
        # vea bien, que es justo el caso que confunde al usuario.
        "QComboBox QAbstractItemView { "
        f"background: #FFFFFF; color: {TEXT_PRIMARY}; "
        f"border: 1px solid {INPUT_BORDER}; outline: none; "
        f"selection-background-color: {PRIMARY_TINT_STRONG}; "
        f"selection-color: {PRIMARY}; "
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
        f"background-color: #FFFFFF; color: {ERROR}; "
        f"border: 1px solid {ERROR}; border-radius: {RADIUS}px; "
        f"padding: {padding}; font-weight: 600; "
        f"font-family: {BODY_FONT_FAMILY}; "
        "}"
        f"QPushButton:hover {{ background-color: {ERROR}; color: white; }}"
        f"QPushButton:pressed {{ background-color: #962D21; color: white; }}"
        f"QPushButton:disabled {{ color: {TEXT_MUTED}; background-color: {APP_BACKGROUND}; "
        f"border: 1px solid {DIVIDER}; }}"
    )


def line_edit_style() -> str:
    """Hoja de FormInput (y de los QLineEdit de los diálogos estándar, vía
    app_stylesheet()). Los campos de sólo lectura se pintan con el fondo de la
    página: así se distingue a simple vista lo que el operador completa de lo
    que el sistema calcula (p. ej. "Monto a cobrar")."""
    return (
        "QLineEdit { "
        f"border: 1px solid {INPUT_BORDER}; border-radius: {RADIUS}px; "
        "padding: 8px 10px; font-size: 14px; "
        f"background: #FFFFFF; color: {TEXT_PRIMARY}; "
        f"font-family: {BODY_FONT_FAMILY}; "
        f"selection-background-color: {PRIMARY_TINT_STRONG}; "
        f"selection-color: {PRIMARY}; "
        "}"
        f"QLineEdit:hover {{ border: 1px solid {INPUT_BORDER_HOVER}; }}"
        f"QLineEdit:focus {{ border: 1px solid {PRIMARY}; }}"
        f'QLineEdit[readOnly="true"] {{ background: {APP_BACKGROUND}; '
        f"border: 1px solid {DIVIDER}; color: {TEXT_PRIMARY}; }}"
        f"QLineEdit:disabled {{ background: {APP_BACKGROUND}; color: {TEXT_MUTED}; "
        f"border: 1px solid {DIVIDER}; }}"
        f'QLineEdit[error="true"] {{ border: 1px solid {ERROR}; '
        f"background: {ERROR_TINT}; }}"
    )


def app_stylesheet() -> str:
    """Hoja global de la aplicación (main.py).

    Cubre lo que las vistas no construyen ellas mismas y que, sin estilo,
    cae en la paleta del sistema: los diálogos estándar (QMessageBox,
    QInputDialog -- p. ej. el motivo al eliminar un préstamo o un cobro),
    las barras de progreso (que en Windows salían con un relleno marrón),
    las barras de desplazamiento y el tooltip (ver tooltip_style()).
    """
    return (
        "QMessageBox, QInputDialog { background-color: #FFFFFF; }"
        f"QMessageBox QLabel, QInputDialog QLabel {{ color: {TEXT_PRIMARY}; "
        "font-size: 13px; }"
        + secondary_button_style(padding="6px 18px").replace(
            "QPushButton", "QMessageBox QPushButton"
        )
        + secondary_button_style(padding="6px 18px").replace(
            "QPushButton", "QInputDialog QPushButton"
        )
        + line_edit_style().replace("QLineEdit", "QInputDialog QLineEdit")
        + "QProgressBar { "
        f"background: {DIVIDER}; border: none; border-radius: 2px; "
        "}"
        f"QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}"
        # Barras de desplazamiento finas: la nativa de Windows es ancha y
        # gris, y en las páginas largas (Caja, detalle del préstamo) se lleva
        # la atención.
        "QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }"
        "QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }"
        f"QScrollBar::handle {{ background: {INPUT_BORDER}; border-radius: 3px; "
        "min-height: 32px; min-width: 32px; }"
        f"QScrollBar::handle:hover {{ background: {INPUT_BORDER_HOVER}; }}"
        "QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }"
        "QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }"
        + tooltip_style()
    )
