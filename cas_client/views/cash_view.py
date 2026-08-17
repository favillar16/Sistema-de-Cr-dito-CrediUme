"""Pantalla de Caja (BR-CAJA-001..004): apertura del turno, cobro de cuotas en
ventanilla, movimientos de efectivo del día y cierre con arqueo, más el
historial de arqueos.

Es la pantalla principal del rol Cajero, y por eso concentra el cobro: el
cajero busca acá al cliente que está abonando, elige la cuota y registra el
pago sin salir de la caja. `LoansView` sigue teniendo su propio cobro para los
demás roles, y ambos llaman a la misma RPC (`RecordPayment`) -- lo que cambia
es desde dónde se llega, no la regla.

La vista nunca calcula el monto esperado por su cuenta: siempre muestra el
`expected_amount` que devuelve el servidor, porque es ese número -- y no uno
recalculado acá -- el que se usa para el arqueo del cierre.
"""

import grpc
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cas_client import documents, documents_docx, theme
from cas_client.formatting import (
    DISPLAY_DATE_PLACEHOLDER,
    fecha,
    fecha_a_iso,
    fecha_hora,
    gs,
)
from cas_client.grpc_client import (
    ApiError,
    CashServiceClient,
    ClientServiceClient,
    LoanServiceClient,
)
from cas_client.rbac_ui import can_supervise_cash_sessions
from cas_client.session import Session
from cas_client.widgets.async_worker import AsyncWorker
from cas_client.widgets.base_view import BaseView
from cas_client.widgets.card import card, labeled_field, section_label, stat_tile
from cas_client.widgets.currency_input import CurrencyInput
from cas_client.widgets.form_input import FormInput
from cas_client.widgets.responsive_grid import ResponsiveGrid
from cas_client.widgets.table import size_columns, style_table
from cas_client.widgets.toast import Toast

_MOVEMENT_HEADERS = ("Hora", "Tipo", "Concepto", "Monto (Gs)", "Origen")
_HISTORY_HEADERS = (
    "Apertura",
    "Cajero",
    "Estado",
    "Inicial (Gs)",
    "Ingresos (Gs)",
    "Egresos (Gs)",
    "Esperado (Gs)",
    "Contado (Gs)",
    "Diferencia (Gs)",
)

_MOVEMENT_TYPES = (("Ingreso", "INGRESO"), ("Egreso", "EGRESO"))

_STATUS_LABELS = {"OPEN": "Abierta", "CLOSED": "Cerrada"}

_CLIENT_HEADERS = ("Cliente", "Documento", "Teléfono")

# Efectivo va primero acá, al revés que en loans_view.py: esta pantalla ES la
# caja, así que el cobro en ventanilla es el caso normal y la transferencia la
# excepción. El valor viaja tal cual al servidor (BR-CAJA-004).
_PAYMENT_METHODS = (
    ("Efectivo (caja)", "EFECTIVO"),
    ("Transferencia / descuento", "TRANSFERENCIA"),
)

# Solo se cobra sobre préstamos en curso: un PENDING/APPROVED todavía no tiene
# cuotas exigibles y un PAID/DEFAULTED/EXPIRED ya no las tiene.
_COBRABLE = "ACTIVE"


def _friendly_message(exc: Exception) -> str:
    if isinstance(exc, ApiError):
        if exc.code == grpc.StatusCode.ALREADY_EXISTS:
            return "Ya tiene una caja abierta. Ciérrela antes de abrir otra."
        if exc.code == grpc.StatusCode.FAILED_PRECONDITION:
            # El servidor distingue varios casos con este código (no hay caja
            # abierta, egreso mayor al efectivo disponible, turno ya cerrado)
            # y su mensaje ya es específico y en español -- se muestra tal
            # cual en vez de aplanarlos todos en una frase genérica.
            return exc.message
        if exc.code == grpc.StatusCode.PERMISSION_DENIED:
            return "No tiene permisos para esta operación de caja."
        if exc.code == grpc.StatusCode.INVALID_ARGUMENT:
            return exc.message
        if exc.code == grpc.StatusCode.UNAVAILABLE:
            return "No se pudo conectar con el servidor."
        return "No se pudo completar la operación de caja. Intente nuevamente."
    return f"No se pudo conectar con el servidor: {exc}"


def _friendly_collection_message(exc: Exception) -> str:
    """Separado de _friendly_message porque los mismos códigos significan
    otra cosa acá: un FAILED_PRECONDITION en la caja es "no hay turno
    abierto", y en el cobro puede ser "el préstamo no está activo" o "la
    cuota ya está saldada"."""
    if isinstance(exc, ApiError):
        if exc.code == grpc.StatusCode.NOT_FOUND:
            return "No se encontró el cliente o el préstamo indicado."
        if exc.code in (
            grpc.StatusCode.FAILED_PRECONDITION,
            grpc.StatusCode.INVALID_ARGUMENT,
        ):
            # El servidor ya devuelve mensajes específicos y en español para
            # estos casos (cuota saldada, préstamo no activo, sin caja
            # abierta); aplanarlos perdería el motivo real.
            return exc.message
        if exc.code == grpc.StatusCode.PERMISSION_DENIED:
            return "No tiene permisos para registrar cobros."
        if exc.code == grpc.StatusCode.UNAVAILABLE:
            return "No se pudo conectar con el servidor."
        return "No se pudo registrar el cobro. Intente nuevamente."
    return f"No se pudo conectar con el servidor: {exc}"


def _signed_gs(value: str) -> str:
    """gs() no conserva el signo de una diferencia negativa (un faltante),
    porque descarta el signo al pasar por int(); acá el signo es justamente
    el dato -- sobrante contra faltante."""
    if value.startswith("-"):
        return f"-{gs(value[1:])}"
    return gs(value)


def _difference_color(value: str) -> str:
    if not value or value.lstrip("-").replace(".", "").strip("0") == "":
        return theme.SUCCESS  # cuadró exacto
    return theme.ERROR


class CashView(BaseView):
    """Caja del usuario autenticado. Se refresca en cada `showEvent` como
    DashboardView: el estado de la caja puede haber cambiado por un cobro en
    efectivo registrado desde la pantalla de préstamos."""

    def __init__(
        self,
        client: CashServiceClient,
        clients_client: ClientServiceClient,
        loans_client: LoanServiceClient,
        session: Session,
        parent: QWidget | None = None,
    ):
        super().__init__("Caja", parent=parent)
        self._client = client
        # El cobro de cuotas vive en esta pantalla (es la ventanilla), así que
        # necesita los otros dos servicios: buscar al cliente que abona y leer
        # su cronograma. MainWindow pasa las mismas instancias que usan
        # ClientsView/LoansView en vez de abrir canales nuevos.
        self._clients_client = clients_client
        self._loans_client = loans_client
        self._session = session
        self._worker: AsyncWorker | None = None
        self._history_worker: AsyncWorker | None = None
        self._collection_worker: AsyncWorker | None = None
        # Cliente y préstamo elegidos para el cobro en curso, y el último pago
        # registrado (BR-LOAN-011: el comprobante describe UN pago concreto y
        # el servidor no expone historial de pagos, así que solo se puede
        # emitir el de la sesión actual).
        self._selected_client = None
        self._selected_loan = None
        self._last_payment = None
        self._detail = None  # último CashSessionDetail abierto, o None
        # Se fija de verdad en set_user(); el valor inicial importa porque la
        # vista se construye antes del login (igual que el resto del shell).
        self._is_supervisor = False
        # Mismo patrón que DashboardView._refresh_generation: una consulta en
        # vuelo que quedó vieja no debe pisar la respuesta de una más nueva
        # ni esconder su barra de progreso (ES-006 §3.5).
        self._refresh_generation = 0

        header_row = QHBoxLayout()
        header_row.setSpacing(12)
        self._status_label = QLabel("")
        self._status_label.setStyleSheet(f"font-size: 14px; color: {theme.TEXT_MUTED};")
        self._status_label.setWordWrap(True)
        header_row.addWidget(self._status_label, stretch=1)

        refresh_button = QPushButton("Actualizar")
        refresh_button.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_button.setStyleSheet(theme.secondary_button_style())
        refresh_button.clicked.connect(self.refresh)
        header_row.addWidget(refresh_button, alignment=Qt.AlignmentFlag.AlignTop)
        self.content_layout.addLayout(header_row)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(4)
        self._progress.hide()
        self.content_layout.addWidget(self._progress)

        self._totals_grid = ResponsiveGrid(min_cell_width=220)
        self.content_layout.addWidget(self._totals_grid)

        self._open_card = self._build_open_card()
        self.content_layout.addWidget(self._open_card)

        # Va antes que movimientos y cierre a propósito: cobrar una cuota es
        # lo que el cajero hace decenas de veces por turno; los movimientos
        # manuales y el arqueo son ocasionales.
        self._collection_section = section_label("Cobro de cuotas")
        self.content_layout.addWidget(self._collection_section)
        self._collection_card = self._build_collection_card()
        self.content_layout.addWidget(self._collection_card)

        self._movement_section = section_label("Movimientos del turno")
        self.content_layout.addWidget(self._movement_section)
        self._movement_card = self._build_movement_card()
        self.content_layout.addWidget(self._movement_card)

        self._close_section = section_label("Cierre y arqueo")
        self.content_layout.addWidget(self._close_section)
        self._close_card = self._build_close_card()
        self.content_layout.addWidget(self._close_card)

        self.content_layout.addWidget(section_label("Historial de arqueos"))
        self._history_card = self._build_history_card()
        self.content_layout.addWidget(self._history_card)

        self.content_layout.addStretch()

        self._toast = Toast(self)
        self._render(None)

    # ---- Construcción de tarjetas ---------------------------------------

    def _build_open_card(self) -> QWidget:
        frame, layout = card()

        intro = QLabel(
            "Declare el efectivo con el que inicia el turno. Ese monto es el "
            "punto de partida del arqueo de cierre."
        )
        intro.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px;")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        grid = ResponsiveGrid(min_cell_width=220)
        monto_field, self._opening_amount = labeled_field(
            "Monto inicial (Gs)", "0", required=True, input_cls=CurrencyInput
        )
        grid.add_widget(monto_field)
        notas_field, self._opening_notes = labeled_field("Observaciones", "Opcional")
        grid.add_widget(notas_field)
        layout.addWidget(grid)

        actions = QHBoxLayout()
        open_button = QPushButton("Abrir caja")
        open_button.setCursor(Qt.CursorShape.PointingHandCursor)
        open_button.setStyleSheet(theme.accent_button_style())
        open_button.clicked.connect(self._on_open_clicked)
        actions.addWidget(open_button)
        actions.addStretch()
        layout.addLayout(actions)

        return frame

    def _build_collection_card(self) -> QWidget:
        """Buscar al cliente que abona -> elegir su préstamo -> elegir la cuota
        -> registrar el cobro -> emitir el comprobante. Todo en una tarjeta,
        de arriba hacia abajo, porque es la secuencia real de la ventanilla."""
        frame, layout = card()

        intro = QLabel(
            "Busque al cliente que está abonando, elija la cuota y registre el "
            "cobro. El monto lo fija el sistema a partir del cronograma."
        )
        intro.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px;")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # -- Paso 1: buscar el cliente --
        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self._client_search = FormInput("Buscar por nombre, documento o teléfono")
        self._client_search.returnPressed.connect(self._on_client_search)
        search_row.addWidget(self._client_search, stretch=1)
        search_button = QPushButton("Buscar cliente")
        search_button.setCursor(Qt.CursorShape.PointingHandCursor)
        search_button.setStyleSheet(theme.secondary_button_style())
        search_button.clicked.connect(self._on_client_search)
        search_row.addWidget(search_button)
        layout.addLayout(search_row)

        self._client_table = QTableWidget(0, len(_CLIENT_HEADERS))
        self._client_table.setHorizontalHeaderLabels(_CLIENT_HEADERS)
        size_columns(self._client_table, stretch_column=0)
        self._client_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._client_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._client_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        # Un clic simple alcanza: el cajero tiene al cliente enfrente y el
        # doble clic (la convención de ClientsView, donde la lista es de
        # navegación) sería un paso de más en una tarea repetitiva.
        self._client_table.cellClicked.connect(self._on_client_selected)
        self._client_table.setMaximumHeight(160)
        style_table(self._client_table)
        self._client_table.setVisible(False)
        layout.addWidget(self._client_table)

        # -- Paso 2: elegir préstamo y cuota --
        self._collection_detail = QWidget()
        detail_layout = QVBoxLayout(self._collection_detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(10)

        self._selected_client_label = QLabel("")
        self._selected_client_label.setStyleSheet(
            f"color: {theme.PRIMARY}; font-size: 14px; font-weight: 600;"
        )
        self._selected_client_label.setWordWrap(True)
        detail_layout.addWidget(self._selected_client_label)

        selectors = ResponsiveGrid(min_cell_width=240)
        selectors.add_widget(
            self._combo_field("Préstamo", "_loan_combo", self._on_loan_changed)
        )
        selectors.add_widget(
            self._combo_field(
                "Cuota a cobrar", "_installment_combo", self._on_installment_changed
            )
        )
        selectors.add_widget(
            self._combo_field("Medio de pago", "_method_combo", self._on_method_changed)
        )
        amount_field, self._collection_amount = labeled_field(
            "Monto a cobrar (Gs)", input_cls=CurrencyInput
        )
        # El monto sale del cronograma y el servidor lo recalcula igual
        # (BR-LOAN-010): se muestra para confirmar, no para editar.
        self._collection_amount.setReadOnly(True)
        selectors.add_widget(amount_field)
        reference_field, self._collection_reference = labeled_field(
            "Código/número de transferencia"
        )
        selectors.add_widget(reference_field)
        detail_layout.addWidget(selectors)

        # Se puebla DESPUÉS de crear el campo de referencia: el primer
        # addItem() mueve el índice de -1 a 0 y dispara _on_method_changed,
        # que lo toca. Poblarlo junto al combo reventaría con AttributeError.
        for label, value in _PAYMENT_METHODS:
            self._method_combo.addItem(label, value)

        self._loan_summary = QLabel("")
        self._loan_summary.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px;")
        self._loan_summary.setWordWrap(True)
        detail_layout.addWidget(self._loan_summary)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self._collect_button = QPushButton("Registrar cobro")
        self._collect_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._collect_button.setStyleSheet(theme.accent_button_style())
        self._collect_button.clicked.connect(self._on_collect)
        actions.addWidget(self._collect_button)

        # BR-LOAN-011: se habilitan recién cuando hay un pago registrado en
        # esta sesión -- el comprobante describe ese pago, no el préstamo.
        self._receipt_buttons: list[QPushButton] = []
        for label, handler in (
            ("Comprobante PDF", self._on_receipt_pdf),
            ("Comprobante DOCX", self._on_receipt_docx),
            ("Imprimir comprobante", self._on_receipt_print),
        ):
            button = QPushButton(label)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(theme.secondary_button_style())
            button.clicked.connect(handler)
            button.setEnabled(False)
            actions.addWidget(button)
            self._receipt_buttons.append(button)
        actions.addStretch()
        detail_layout.addLayout(actions)

        self._collection_detail.setVisible(False)
        layout.addWidget(self._collection_detail)

        self._collection_progress = QProgressBar()
        self._collection_progress.setRange(0, 0)
        self._collection_progress.setTextVisible(False)
        self._collection_progress.setFixedHeight(4)
        self._collection_progress.hide()
        layout.addWidget(self._collection_progress)

        return frame

    def _combo_field(self, caption_text: str, attr: str, on_change) -> QWidget:
        """Un QComboBox con la misma leyenda pequeña que labeled_field() pone
        sobre un FormInput -- card.py solo cubre inputs de texto, y esta
        tarjeta mezcla los dos tipos de campo."""
        wrapper = QWidget()
        column = QVBoxLayout(wrapper)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        caption = QLabel(caption_text)
        caption.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 11px; font-weight: 600;"
        )
        column.addWidget(caption)
        combo = QComboBox()
        combo.setCursor(Qt.CursorShape.PointingHandCursor)
        combo.currentIndexChanged.connect(on_change)
        column.addWidget(combo)
        setattr(self, attr, combo)
        return wrapper

    def _build_movement_card(self) -> QWidget:
        frame, layout = card()

        grid = ResponsiveGrid(min_cell_width=200)
        tipo_wrapper = QWidget()
        tipo_layout = QVBoxLayout(tipo_wrapper)
        tipo_layout.setContentsMargins(0, 0, 0, 0)
        tipo_layout.setSpacing(4)
        tipo_caption = QLabel("Tipo")
        tipo_caption.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 11px; font-weight: 600;"
        )
        tipo_layout.addWidget(tipo_caption)
        self._movement_type = QComboBox()
        for label, value in _MOVEMENT_TYPES:
            self._movement_type.addItem(label, value)
        self._movement_type.setCursor(Qt.CursorShape.PointingHandCursor)
        tipo_layout.addWidget(self._movement_type)
        grid.add_widget(tipo_wrapper)

        monto_field, self._movement_amount = labeled_field(
            "Monto (Gs)", "0", required=True, input_cls=CurrencyInput
        )
        grid.add_widget(monto_field)
        concepto_field, self._movement_concept = labeled_field(
            "Concepto", "Ej. Reposición de fondo", required=True
        )
        grid.add_widget(concepto_field)
        layout.addWidget(grid)

        actions = QHBoxLayout()
        register_button = QPushButton("Registrar movimiento")
        register_button.setCursor(Qt.CursorShape.PointingHandCursor)
        register_button.setStyleSheet(theme.accent_button_style())
        register_button.clicked.connect(self._on_movement_clicked)
        actions.addWidget(register_button)
        actions.addStretch()
        layout.addLayout(actions)

        self._movement_table = QTableWidget(0, len(_MOVEMENT_HEADERS))
        self._movement_table.setHorizontalHeaderLabels(_MOVEMENT_HEADERS)
        size_columns(self._movement_table, stretch_column=2)  # "Concepto"
        self._movement_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._movement_table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        style_table(self._movement_table)
        layout.addWidget(self._movement_table)

        self._movement_empty = QLabel("Todavía no hay movimientos en este turno.")
        self._movement_empty.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 12px;"
        )
        layout.addWidget(self._movement_empty)

        return frame

    def _build_close_card(self) -> QWidget:
        frame, layout = card()

        intro = QLabel(
            "Cuente el efectivo en caja y declare el total. El sistema compara "
            "ese conteo contra lo que debería haber y registra la diferencia."
        )
        intro.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px;")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        grid = ResponsiveGrid(min_cell_width=220)
        contado_field, self._counted_amount = labeled_field(
            "Efectivo contado (Gs)", "0", required=True, input_cls=CurrencyInput
        )
        grid.add_widget(contado_field)
        notas_field, self._closing_notes = labeled_field(
            "Observaciones", "Ej. explicación de la diferencia"
        )
        grid.add_widget(notas_field)
        layout.addWidget(grid)

        actions = QHBoxLayout()
        close_button = QPushButton("Cerrar caja")
        close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        close_button.setStyleSheet(theme.accent_button_style())
        close_button.clicked.connect(self._on_close_clicked)
        actions.addWidget(close_button)
        actions.addStretch()
        layout.addLayout(actions)

        return frame

    def _build_history_card(self) -> QWidget:
        frame, layout = card()

        self._history_caption = QLabel("")
        self._history_caption.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 12px;"
        )
        self._history_caption.setWordWrap(True)
        layout.addWidget(self._history_caption)

        grid = ResponsiveGrid(min_cell_width=200)
        desde_field, self._history_start = labeled_field(
            "Desde", DISPLAY_DATE_PLACEHOLDER
        )
        grid.add_widget(desde_field)
        hasta_field, self._history_end = labeled_field(
            "Hasta", DISPLAY_DATE_PLACEHOLDER
        )
        grid.add_widget(hasta_field)
        layout.addWidget(grid)

        actions = QHBoxLayout()
        search_button = QPushButton("Buscar arqueos")
        search_button.setCursor(Qt.CursorShape.PointingHandCursor)
        search_button.setStyleSheet(theme.secondary_button_style())
        search_button.clicked.connect(self._on_history_clicked)
        actions.addWidget(search_button)
        actions.addStretch()
        layout.addLayout(actions)

        self._history_progress = QProgressBar()
        self._history_progress.setRange(0, 0)
        self._history_progress.setTextVisible(False)
        self._history_progress.setFixedHeight(4)
        self._history_progress.hide()
        layout.addWidget(self._history_progress)

        self._history_table = QTableWidget(0, len(_HISTORY_HEADERS))
        self._history_table.setHorizontalHeaderLabels(_HISTORY_HEADERS)
        size_columns(self._history_table, stretch_column=1)  # "Cajero"
        self._history_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._history_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        style_table(self._history_table)
        layout.addWidget(self._history_table)

        return frame

    # ---- Estado de sesión / refresco ------------------------------------

    def set_user(self, username: str, role: str) -> None:
        self._is_supervisor = can_supervise_cash_sessions(role)
        self._history_caption.setText(
            "Arqueos de todos los cajeros."
            if self._is_supervisor
            else "Sus propios arqueos."
        )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._session.access_token:
            self.refresh()

    def refresh(self) -> None:
        if not self._session.access_token:
            return
        self._refresh_generation += 1
        generation = self._refresh_generation
        self._progress.setRange(0, 0)
        self._progress.show()
        self._worker = AsyncWorker(
            self._client.get_current_cash_session,
            self._session.access_token,
            error_translator=_friendly_message,
        )
        self._worker.succeeded.connect(
            lambda response: self._on_current_loaded(response, generation)
        )
        self._worker.failed.connect(
            lambda message: self._on_failed(message, generation)
        )
        self._worker.finished.connect(lambda: self._hide_progress_for(generation))
        self._worker.start()

    def _hide_progress_for(self, generation: int) -> None:
        if generation != self._refresh_generation:
            return
        # Parkear el rango detiene el timer de la animación indeterminada
        # mientras está oculta -- ver DashboardView._hide_progress().
        self._progress.hide()
        self._progress.setRange(0, 1)
        self._progress.setValue(0)

    def _on_current_loaded(self, response, generation: int) -> None:
        if generation != self._refresh_generation:
            return
        self._render(response.session if response.has_open_session else None)

    def _on_failed(self, message: str, generation: int) -> None:
        if generation != self._refresh_generation:
            return
        self._toast.show_message(message)

    # ---- Render ----------------------------------------------------------

    def _render(self, detail) -> None:
        """`detail` es el CashSessionDetail abierto, o None si no hay turno.

        Ocultar-en-vez-de-deshabilitar, misma convención que el resto del
        cliente: con la caja cerrada no se muestran las tarjetas de
        movimientos ni de cierre, y con la caja abierta no se muestra la de
        apertura.
        """
        self._detail = detail
        abierta = detail is not None

        self._open_card.setVisible(not abierta)
        # El cobro exige caja abierta (BR-CAJA-004): ofrecerlo con la caja
        # cerrada solo produciría un FAILED_PRECONDITION del servidor.
        self._collection_section.setVisible(abierta)
        self._collection_card.setVisible(abierta)
        self._movement_section.setVisible(abierta)
        self._movement_card.setVisible(abierta)
        self._close_section.setVisible(abierta)
        self._close_card.setVisible(abierta)

        self._totals_grid.clear()
        if not abierta:
            self._status_label.setText(
                "No tiene una caja abierta. Ábrala declarando el efectivo inicial "
                "para poder cobrar en efectivo."
            )
            return

        self._status_label.setText(
            f"Caja abierta el {fecha_hora(detail.opened_at.ToDatetime())} "
            f"por {detail.cashier_full_name or detail.cashier_username}."
        )

        for title, value, color, icon in (
            ("Monto inicial (Gs)", detail.opening_amount, theme.PRIMARY, "₲"),
            ("Ingresos (Gs)", detail.total_income, theme.SUCCESS, "IN"),
            ("Egresos (Gs)", detail.total_expense, theme.ERROR, "EG"),
            (
                "Cobros de cuotas (Gs)",
                detail.total_loan_collections,
                theme.ACCENT,
                "CO",
            ),
            ("Efectivo esperado (Gs)", detail.expected_amount, theme.PRIMARY, "="),
        ):
            tile, _ = stat_tile(
                title, accent_color=color, icon_text=icon, value_text=gs(value)
            )
            self._totals_grid.add_widget(tile)

        self._render_movements(detail)
        # Pre-carga el conteo con el esperado: en el caso normal (la caja
        # cuadra) el cajero confirma, y en el caso anormal corrige. No se
        # asume nada -- el servidor recalcula el esperado igual y compara
        # contra lo que quede acá.
        self._counted_amount.set_amount(detail.expected_amount)

    def _render_movements(self, detail) -> None:
        self._movement_table.setRowCount(0)
        for movimiento in detail.movements:
            row = self._movement_table.rowCount()
            self._movement_table.insertRow(row)
            celdas = (
                fecha_hora(movimiento.created_at.ToDatetime()),
                "Ingreso" if movimiento.movement_type == "INGRESO" else "Egreso",
                movimiento.concept,
                gs(movimiento.amount),
                "Cobro de préstamo" if movimiento.is_automatic else "Manual",
            )
            for col, texto in enumerate(celdas):
                item = QTableWidgetItem(texto)
                if col == 3:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self._movement_table.setItem(row, col, item)
        hay_movimientos = detail.movements_count > 0
        self._movement_table.setVisible(hay_movimientos)
        self._movement_empty.setVisible(not hay_movimientos)

    # ---- Acciones --------------------------------------------------------

    def _on_open_clicked(self) -> None:
        if not self._session.access_token:
            return
        monto = self._opening_amount.raw_value()
        # Un monto inicial de 0 es legítimo (caja sin fondo fijo), así que la
        # validación es "que haya escrito algo", no "que sea mayor a cero".
        self._opening_amount.set_error(not monto)
        if not monto:
            self._toast.show_message("Indique el monto inicial (puede ser 0).")
            return
        self._run(
            self._client.open_cash_session,
            self._session.access_token,
            monto,
            self._opening_notes.text().strip(),
            mensaje_ok="Caja abierta.",
        )

    def _on_movement_clicked(self) -> None:
        if not self._session.access_token:
            return
        monto = self._movement_amount.raw_value()
        concepto = self._movement_concept.text().strip()
        self._movement_amount.set_error(not monto or monto.strip("0") == "")
        self._movement_concept.set_error(not concepto)
        if not monto or monto.strip("0") == "" or not concepto:
            self._toast.show_message(
                "Indique un monto mayor a cero y el concepto del movimiento."
            )
            return
        self._run(
            self._client.register_cash_movement,
            self._session.access_token,
            self._movement_type.currentData(),
            monto,
            concepto,
            mensaje_ok="Movimiento registrado.",
            al_terminar=self._clear_movement_form,
        )

    def _clear_movement_form(self) -> None:
        self._movement_amount.clear()
        self._movement_concept.clear()

    def _on_close_clicked(self) -> None:
        if not self._session.access_token:
            return
        contado = self._counted_amount.raw_value()
        self._counted_amount.set_error(not contado)
        if not contado:
            self._toast.show_message("Declare el efectivo contado (puede ser 0).")
            return
        self._run(
            self._client.close_cash_session,
            self._session.access_token,
            contado,
            self._closing_notes.text().strip(),
            mensaje_ok=None,  # el mensaje lo arma _on_closed con la diferencia
            al_terminar=self._clear_close_form,
            on_success=self._on_closed,
        )

    def _clear_close_form(self) -> None:
        self._closing_notes.clear()

    def _on_closed(self, detail) -> None:
        diferencia = detail.closing_difference
        if diferencia.lstrip("-").replace(".", "").strip("0") == "":
            self._toast.show_message("Caja cerrada. El arqueo cuadró exacto.")
        elif diferencia.startswith("-"):
            self._toast.show_message(
                f"Caja cerrada con un faltante de {_signed_gs(diferencia)[1:]}."
            )
        else:
            self._toast.show_message(
                f"Caja cerrada con un sobrante de {_signed_gs(diferencia)}."
            )

    def _run(
        self,
        fn,
        *args,
        mensaje_ok: str | None,
        al_terminar=None,
        on_success=None,
    ) -> None:
        """Toda acción de caja termina igual: mostrar el resultado y volver a
        pedir el estado actual al servidor, en vez de mutar `self._detail` con
        lo que la vista supone que pasó."""
        self._progress.setRange(0, 0)
        self._progress.show()
        self._worker = AsyncWorker(fn, *args, error_translator=_friendly_message)

        def _succeeded(detail) -> None:
            if al_terminar is not None:
                al_terminar()
            if on_success is not None:
                on_success(detail)
            elif mensaje_ok:
                self._toast.show_message(mensaje_ok)
            self.refresh()

        self._worker.succeeded.connect(_succeeded)
        self._worker.failed.connect(self._toast.show_message)
        # Sin `generation`: refresh() bumpea el contador y muestra su propia
        # barra, así que esconderla acá incondicionalmente cubre el caso de
        # error (donde refresh() no llega a correr) sin pisar la del refresco.
        self._worker.finished.connect(self._hide_action_progress)
        self._worker.start()

    def _hide_action_progress(self) -> None:
        self._progress.hide()
        self._progress.setRange(0, 1)
        self._progress.setValue(0)

    # ---- Cobro de cuotas (BR-CAJA-004) -----------------------------------

    def _run_collection(self, fn, *args, on_success, **kwargs) -> None:
        """Toda consulta de la tarjeta de cobro usa su propia barra de
        progreso, para no confundirse con la del estado de la caja."""
        self._collection_progress.setRange(0, 0)
        self._collection_progress.show()
        self._collection_worker = AsyncWorker(
            fn, *args, error_translator=_friendly_collection_message, **kwargs
        )
        self._collection_worker.succeeded.connect(on_success)
        self._collection_worker.failed.connect(self._toast.show_message)
        self._collection_worker.finished.connect(self._hide_collection_progress)
        self._collection_worker.start()

    def _hide_collection_progress(self) -> None:
        self._collection_progress.hide()
        self._collection_progress.setRange(0, 1)
        self._collection_progress.setValue(0)

    def _on_client_search(self) -> None:
        if not self._session.access_token:
            return
        termino = self._client_search.text().strip()
        self._client_search.set_error(not termino)
        if not termino:
            self._toast.show_message(
                "Escriba el nombre, documento o teléfono del cliente."
            )
            return
        self._run_collection(
            self._clients_client.search_clients,
            self._session.access_token,
            termino,
            on_success=self._on_clients_found,
        )

    def _on_clients_found(self, response) -> None:
        self._client_table.setRowCount(0)
        for cliente in response.clients:
            row = self._client_table.rowCount()
            self._client_table.insertRow(row)
            celdas = (
                f"{cliente.first_name} {cliente.last_name}",
                cliente.national_id,
                cliente.phone_number,
            )
            for col, texto in enumerate(celdas):
                item = QTableWidgetItem(texto)
                if col == 0:
                    # La respuesta ya trae la ficha completa (SearchClients
                    # devuelve GetClientByIdResponse), así que el comprobante
                    # puede armarse sin una segunda consulta.
                    item.setData(Qt.ItemDataRole.UserRole, cliente)
                self._client_table.setItem(row, col, item)
        self._client_table.setVisible(True)
        if not response.clients:
            self._toast.show_message("No se encontraron clientes con ese dato.")

    def _on_client_selected(self, row: int, _column: int) -> None:
        item = self._client_table.item(row, 0)
        if item is None:
            return
        cliente = item.data(Qt.ItemDataRole.UserRole)
        self._selected_client = cliente
        self._selected_client_label.setText(
            f"{cliente.first_name} {cliente.last_name} · C.I. {cliente.national_id}"
        )
        self._reset_collection_selection()
        self._run_collection(
            self._loans_client.list_client_loans,
            self._session.access_token,
            cliente.id,
            on_success=self._on_client_loans_loaded,
        )

    def _reset_collection_selection(self) -> None:
        """Un cliente nuevo invalida el préstamo, la cuota y el comprobante
        anterior -- dejarlos habilitados permitiría emitir el comprobante de
        otro cliente sobre la pantalla del actual."""
        self._selected_loan = None
        self._last_payment = None
        self._loan_combo.clear()
        self._installment_combo.clear()
        self._collection_amount.clear()
        self._loan_summary.setText("")
        for button in self._receipt_buttons:
            button.setEnabled(False)

    def _on_client_loans_loaded(self, response) -> None:
        activos = [loan for loan in response.loans if loan.status == _COBRABLE]
        self._loan_combo.clear()
        for loan in activos:
            etiqueta = (
                f"{loan.id[:8]} · {loan.term_months} cuotas · "
                f"saldo {gs(loan.remaining_balance)}"
            )
            self._loan_combo.addItem(etiqueta, loan)
        self._collection_detail.setVisible(True)
        if not activos:
            self._loan_summary.setText(
                "Este cliente no tiene préstamos activos con cuotas por cobrar."
            )
            self._collect_button.setEnabled(False)
            return
        self._collect_button.setEnabled(True)

    def _on_loan_changed(self, index: int) -> None:
        loan = self._loan_combo.itemData(index)
        self._selected_loan = loan
        self._installment_combo.clear()
        self._collection_amount.clear()
        if loan is None:
            return
        estado = (
            "al día"
            if loan.payment_status == "AL_DIA"
            else f"con {loan.overdue_installments_count} cuota(s) vencida(s) "
            f"por {gs(loan.overdue_amount)}"
        )
        self._loan_summary.setText(
            f"Saldo restante {gs(loan.remaining_balance)} · Préstamo {estado}."
        )
        self._run_collection(
            self._loans_client.get_amortization_schedule,
            self._session.access_token,
            loan.id,
            on_success=self._on_schedule_loaded,
        )

    def _on_schedule_loaded(self, response) -> None:
        self._installment_combo.clear()
        for cuota in response.installments:
            if cuota.is_paid:
                continue
            self._installment_combo.addItem(
                f"Cuota {cuota.installment_number} · Vence "
                f"{fecha(cuota.due_date)} · {gs(cuota.amount_due)}",
                (cuota.installment_number, cuota.amount_due),
            )
        hay_pendientes = self._installment_combo.count() > 0
        self._collect_button.setEnabled(hay_pendientes)
        if not hay_pendientes:
            self._loan_summary.setText(
                "Este préstamo no tiene cuotas pendientes de cobro."
            )

    def _on_installment_changed(self, index: int) -> None:
        data = self._installment_combo.itemData(index)
        if data is None:
            self._collection_amount.clear()
            return
        _numero, monto = data
        self._collection_amount.set_amount(monto)

    def _on_method_changed(self, _index: int) -> None:
        """En efectivo no hay referencia que pedir (BR-CAJA-004). Se
        deshabilita en vez de ocultarse, misma decisión que en loans_view."""
        es_efectivo = self._method_combo.currentData() == "EFECTIVO"
        self._collection_reference.setEnabled(not es_efectivo)
        if es_efectivo:
            self._collection_reference.clear()
            self._collection_reference.set_error(False)

    def _on_collect(self) -> None:
        if not self._session.access_token or self._selected_loan is None:
            return
        data = self._installment_combo.itemData(self._installment_combo.currentIndex())
        if data is None:
            self._toast.show_message("Elija la cuota que está abonando.")
            return
        numero_cuota, _monto = data
        medio = self._method_combo.currentData()
        referencia = self._collection_reference.text().strip()
        if medio != "EFECTIVO" and not referencia:
            self._collection_reference.set_error(True)
            self._toast.show_message(
                "Ingrese el código o número de transferencia del pago."
            )
            return
        self._collection_reference.set_error(False)
        # El cobro en efectivo exige caja abierta; el servidor lo rechaza si no
        # la hay, pero la tarjeta ya está oculta en ese caso.
        self._run_collection(
            self._loans_client.record_payment,
            self._session.access_token,
            self._selected_loan.id,
            referencia,
            installment_number=numero_cuota,
            payment_method=medio,
            on_success=self._on_payment_recorded,
        )

    def _on_payment_recorded(self, payment) -> None:
        self._last_payment = payment
        self._collection_reference.clear()
        for button in self._receipt_buttons:
            button.setEnabled(True)
        cuotas = documents.cuotas_cubiertas_texto(
            payment.covered_installments, payment.total_installments
        )
        self._toast.show_message(
            f"Cobro registrado por {gs(payment.amount_paid)} — {cuotas}. "
            "Ya puede emitir el comprobante."
        )
        # Refresca el saldo del préstamo y las cuotas pendientes, y (si fue en
        # efectivo) los totales de la caja, que acaban de cambiar.
        self._reload_selected_loan()
        self.refresh()

    def _reload_selected_loan(self) -> None:
        if self._selected_loan is None:
            return
        self._run_collection(
            self._loans_client.get_loan_by_id,
            self._session.access_token,
            self._selected_loan.id,
            on_success=self._on_selected_loan_reloaded,
        )

    def _on_selected_loan_reloaded(self, loan) -> None:
        self._selected_loan = loan
        indice = self._loan_combo.currentIndex()
        if indice >= 0:
            self._loan_combo.setItemData(indice, loan)
        estado = "cancelado" if loan.status == "PAID" else "activo"
        self._loan_summary.setText(
            f"Saldo restante {gs(loan.remaining_balance)} · Préstamo {estado}."
        )
        if loan.status != _COBRABLE:
            # Quedó saldado con este cobro: no hay más cuotas que ofrecer.
            self._installment_combo.clear()
            self._collection_amount.clear()
            self._collect_button.setEnabled(False)
            return
        self._run_collection(
            self._loans_client.get_amortization_schedule,
            self._session.access_token,
            loan.id,
            on_success=self._on_schedule_loaded,
        )

    # ---- Comprobante de pago (BR-LOAN-011) -------------------------------

    def _receipt_html(self) -> str:
        return documents.comprobante_pago_html(
            self._selected_loan, self._selected_client, self._last_payment
        )

    def _receipt_name(self, extension: str) -> str:
        return f"comprobante_{self._selected_loan.id[:8]}.{extension}"

    def _on_receipt_pdf(self) -> None:
        if self._last_payment is None:
            return
        path, _filtro = QFileDialog.getSaveFileName(
            self, "Guardar comprobante", self._receipt_name("pdf"), "PDF (*.pdf)"
        )
        if not path:
            return
        document = QTextDocument()
        document.setHtml(self._receipt_html())
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(path)
        try:
            document.print_(printer)
        except OSError as exc:
            self._toast.show_message(documents.friendly_file_error(exc))
            return
        self._toast.show_message("Comprobante guardado.")

    def _on_receipt_docx(self) -> None:
        if self._last_payment is None:
            return
        path, _filtro = QFileDialog.getSaveFileName(
            self, "Guardar comprobante", self._receipt_name("docx"), "Word (*.docx)"
        )
        if not path:
            return
        try:
            documents_docx.comprobante_pago_docx(
                self._selected_loan, self._selected_client, self._last_payment
            ).save(path)
        except OSError as exc:
            self._toast.show_message(documents.friendly_file_error(exc))
            return
        self._toast.show_message("Comprobante guardado.")

    def _on_receipt_print(self) -> None:
        if self._last_payment is None:
            return
        document = QTextDocument()
        document.setHtml(self._receipt_html())
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        dialog = QPrintDialog(printer, self)
        if dialog.exec() == QPrintDialog.DialogCode.Accepted:
            try:
                document.print_(printer)
            except OSError as exc:
                self._toast.show_message(documents.friendly_file_error(exc))

    # ---- Historial -------------------------------------------------------

    def _on_history_clicked(self) -> None:
        if not self._session.access_token:
            return
        desde = self._history_start.text().strip()
        hasta = self._history_end.text().strip()
        self._history_progress.setRange(0, 0)
        self._history_progress.show()
        self._history_worker = AsyncWorker(
            self._client.list_cash_sessions,
            self._session.access_token,
            fecha_a_iso(desde),
            fecha_a_iso(hasta),
            error_translator=_friendly_message,
        )
        self._history_worker.succeeded.connect(self._on_history_loaded)
        self._history_worker.failed.connect(self._toast.show_message)
        self._history_worker.finished.connect(self._hide_history_progress)
        self._history_worker.start()

    def _hide_history_progress(self) -> None:
        self._history_progress.hide()
        self._history_progress.setRange(0, 1)
        self._history_progress.setValue(0)

    def _on_history_loaded(self, response) -> None:
        self._history_table.setRowCount(0)
        for turno in response.sessions:
            row = self._history_table.rowCount()
            self._history_table.insertRow(row)
            cerrada = turno.status == "CLOSED"
            celdas = (
                fecha_hora(turno.opened_at.ToDatetime()),
                turno.cashier_full_name or turno.cashier_username,
                _STATUS_LABELS.get(turno.status, turno.status),
                gs(turno.opening_amount),
                gs(turno.total_income),
                gs(turno.total_expense),
                gs(turno.expected_amount),
                gs(turno.closing_counted_amount) if cerrada else "—",
                _signed_gs(turno.closing_difference) if cerrada else "—",
            )
            for col, texto in enumerate(celdas):
                item = QTableWidgetItem(texto)
                if col >= 3:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                # La diferencia es la única columna que se colorea: verde
                # cuando el arqueo cuadró, rojo cuando no. El resto son
                # montos neutros que no significan nada por sí solos.
                if col == 8 and cerrada:
                    item.setForeground(
                        QColor(_difference_color(turno.closing_difference))
                    )
                self._history_table.setItem(row, col, item)
        if not response.sessions:
            self._toast.show_message("No hay arqueos en ese rango.")
