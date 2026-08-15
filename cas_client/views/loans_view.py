from decimal import Decimal, InvalidOperation

import grpc
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QTextDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
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
    gs,
    rate_percent,
)
from cas_client.grpc_client import ApiError, ClientServiceClient, LoanServiceClient
from cas_client.rbac_ui import (
    can_edit_installment_amount,
    can_edit_interest_rate,
    fixed_interest_rate_percent,
    role_at_least,
)
from cas_client.session import Session
from cas_client.widgets.async_worker import AsyncWorker
from cas_client.widgets.base_view import BaseView
from cas_client.widgets.card import card, labeled_field, section_label, stat_tile
from cas_client.widgets.currency_input import CurrencyInput
from cas_client.widgets.form_input import FormInput
from cas_client.widgets.responsive_grid import ResponsiveGrid
from cas_client.widgets.scroll_area import wrap_scrollable
from cas_client.widgets.table import size_columns, style_table
from cas_client.widgets.toast import Toast

(
    _PAGE_SEARCH,
    _PAGE_CREATE,
    _PAGE_DETAIL,
    _PAGE_SCHEDULE,
    _PAGE_EDIT_PROPOSAL,
    _PAGE_ACTIVE_LOANS,
) = range(6)

_LOANS_TABLE_HEADERS = (
    "Estado",
    "Cuotas",
    "Capital (Gs)",
    "Tasa de interés",
    "Plazo (meses)",
    "Saldo restante (Gs)",
)

_ACTIVE_LOANS_TABLE_HEADERS = (
    "Cliente",
    "Cuotas",
    "Capital (Gs)",
    "Tasa de interés",
    "Plazo (meses)",
    "Saldo restante (Gs)",
)

_SCHEDULE_TABLE_HEADERS = (
    "Cuota",
    "Vencimiento",
    "Monto cuota (Gs)",
    "Capital (Gs)",
    "Interés (Gs)",
    "Saldo restante (Gs)",
    "",
)
_SCHEDULE_COL_ADJUST = len(_SCHEDULE_TABLE_HEADERS) - 1

_ESTADOS_LABEL = {
    "PENDING": "Pendiente",
    "APPROVED": "Aprobado",
    "ACTIVE": "Activo",
    "PAID": "Pagado",
    "DEFAULTED": "Incumplido",
    # LoanStatusEnum.EXPIRED se muestra como "Rechazado" por decisión de
    # producto (antes "Caducado") -- el estado del servidor no cambió, solo la
    # etiqueta que ve el usuario. Ver documents.py, que tiene su propia copia
    # de este mapa para los documentos generados.
    "EXPIRED": "Rechazado",
}

# (background, foreground) per status -- single source of truth now lives in
# theme.LOAN_STATUS_COLORS so dashboard_view.py's stat tiles can reuse the same
# mapping without importing from this view module.
_STATUS_STYLE = theme.LOAN_STATUS_COLORS

_DOCUMENT_LABELS = {
    "liquidacion": "Liquidación de préstamo",
    "pagare": "Pagaré",
    "contrato": "Contrato",
    "cronograma": "Cronograma de pago (para el cliente)",
    # BR-LOAN-011. A diferencia de los otros cuatro, este no se puede armar en
    # cualquier momento a partir del préstamo: describe UN pago puntual, así
    # que solo se habilita después de registrar uno (ver _last_payment).
    "comprobante": "Comprobante del último pago registrado",
}


_PAYMENT_STATUS_LABEL = {
    "AL_DIA": "Al día",
    "CUOTA_VENCIDA": "Cuota vencida",
}


def _cuotas_texto_y_color(loan) -> tuple[str, str]:
    """Texto/color de la columna "Cuotas" -- BR-LOAN-009's payment_status
    solo viene poblado para préstamos ACTIVE (el resto de los estados ya se
    explican solos vía la columna "Estado", así que muestran "-" acá en vez
    de repetir la misma info dos veces)."""
    if loan.payment_status == "CUOTA_VENCIDA":
        return f"Cuota vencida ({loan.overdue_installments_count})", theme.ERROR
    if loan.payment_status == "AL_DIA":
        return "Al día", theme.SUCCESS
    return "-", theme.TEXT_MUTED


def _apply_status_badge(label: QLabel, status: str) -> None:
    label.setText(_ESTADOS_LABEL.get(status, status))
    bg, fg = _STATUS_STYLE.get(status, (theme.TEXT_MUTED, "white"))
    label.setStyleSheet(
        f"background-color: {bg}; color: {fg}; border-radius: 10px; "
        "padding: 3px 10px; font-size: 12px; font-weight: 600;"
    )


def _friendly_message(exc: Exception) -> str:
    if isinstance(exc, ApiError):
        if exc.code == grpc.StatusCode.NOT_FOUND:
            return "No se encontró el préstamo o el cliente."
        if exc.code == grpc.StatusCode.INVALID_ARGUMENT:
            return f"Datos inválidos: {exc.message}"
        if exc.code == grpc.StatusCode.FAILED_PRECONDITION:
            return exc.message
        if exc.code == grpc.StatusCode.PERMISSION_DENIED:
            return "No tiene permisos para realizar esta acción."
        if exc.code == grpc.StatusCode.UNAVAILABLE:
            return "No se pudo conectar con el servidor."
        return "Ocurrió un error inesperado. Intente nuevamente."
    return f"No se pudo conectar con el servidor: {exc}"


# Promovido a documents.py una vez que dashboard_view.py (reporte de cierre de
# período) pasó a ser un segundo call site del mismo manejo de error de E/S.
_friendly_file_error = documents.friendly_file_error


class LoansView(BaseView):
    """Ciclo de vida de préstamos -- sistema francés (cuota fija), specs/loans/README."""

    view_client_requested = Signal(str)  # client_id

    def __init__(
        self,
        client: LoanServiceClient,
        client_service: ClientServiceClient,
        session: Session,
        parent: QWidget | None = None,
    ):
        super().__init__(title="Préstamos", parent=parent)
        self._client = client
        self._client_service = client_service
        self._session = session
        self._worker: AsyncWorker | None = None
        self._current_client_id: str | None = None
        self._selected_loan_id: str | None = None
        self._detail_loan = None
        self._pending_document: tuple[str, str] | None = None
        # BR-LOAN-011: RecordPaymentResponse del último pago registrado en
        # esta sesión, junto al préstamo al que corresponde. El servidor no
        # expone un historial de pagos, así que el comprobante solo puede
        # emitirse para el pago recién hecho -- se descarta al cambiar de
        # préstamo para no emitir un comprobante contra el préstamo equivocado.
        self._last_payment = None
        self._last_payment_loan_id: str | None = None
        self._pending_schedule_after_create = False

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(4)
        self._progress.hide()
        self.content_layout.addWidget(self._progress)

        self._stack = QStackedWidget()
        self.content_layout.addWidget(self._stack)

        self._stack.addWidget(self._build_search_page())
        self._stack.addWidget(wrap_scrollable(self._build_create_page()))
        self._stack.addWidget(wrap_scrollable(self._build_detail_page()))
        self._stack.addWidget(self._build_schedule_page())
        self._stack.addWidget(wrap_scrollable(self._build_edit_proposal_page()))
        self._stack.addWidget(self._build_active_loans_page())

        self._toast = Toast(self)

    def load_client(self, client_id: str, display_name: str) -> None:
        """Entry point used by MainWindow when navigating here from ClientsView."""
        self._stack.setCurrentIndex(_PAGE_SEARCH)
        self._show_loans_for(client_id, display_name)

    # ---- Página de búsqueda ---------------------------------------------

    def _build_search_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self._search_title = QLabel("")
        self._search_title.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        layout.addWidget(self._search_title)

        top_toolbar = QHBoxLayout()
        self._client_search_input = FormInput("Buscar cliente por nombre o documento")
        self._client_search_input.returnPressed.connect(self._on_client_search_submit)
        top_toolbar.addWidget(self._client_search_input, stretch=1)

        client_search_button = QPushButton("Buscar cliente")
        client_search_button.setStyleSheet(theme.secondary_button_style())
        client_search_button.clicked.connect(self._on_client_search_submit)
        top_toolbar.addWidget(client_search_button)

        active_loans_button = QPushButton("Ver préstamos activos")
        active_loans_button.setStyleSheet(theme.secondary_button_style())
        active_loans_button.clicked.connect(self._show_active_loans_page)
        top_toolbar.addWidget(active_loans_button)

        new_button = QPushButton("Nuevo préstamo")
        new_button.setStyleSheet(theme.accent_button_style())
        new_button.clicked.connect(self._show_create_page)
        top_toolbar.addWidget(new_button)
        layout.addLayout(top_toolbar)

        # ---- Sub-estado 1: elegir cliente -----------------------------
        self._client_search_section = QWidget()
        search_section_layout = QVBoxLayout(self._client_search_section)
        search_section_layout.setContentsMargins(0, 0, 0, 0)
        search_section_layout.setSpacing(8)

        self._client_results_table = QTableWidget(0, 2)
        self._client_results_table.setHorizontalHeaderLabels(("Nombre", "Documento"))
        size_columns(self._client_results_table, stretch_column=0)
        self._client_results_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._client_results_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._client_results_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._client_results_table.cellDoubleClicked.connect(
            self._on_client_row_activated
        )
        self._client_results_table.itemSelectionChanged.connect(
            self._update_picker_buttons
        )
        style_table(self._client_results_table)
        search_section_layout.addWidget(self._client_results_table, stretch=1)

        picker_toolbar = QHBoxLayout()
        self._show_loans_button = QPushButton("Ver préstamos")
        self._show_loans_button.setStyleSheet(theme.secondary_button_style())
        self._show_loans_button.setEnabled(False)
        self._show_loans_button.clicked.connect(self._show_loans_for_selected)
        picker_toolbar.addWidget(self._show_loans_button)

        self._view_client_button = QPushButton("Ver ficha del cliente")
        self._view_client_button.setStyleSheet(theme.secondary_button_style())
        self._view_client_button.setEnabled(False)
        self._view_client_button.clicked.connect(self._view_client_for_selected)
        picker_toolbar.addWidget(self._view_client_button)
        picker_toolbar.addStretch()
        search_section_layout.addLayout(picker_toolbar)

        layout.addWidget(self._client_search_section, stretch=1)

        # ---- Sub-estado 2: préstamos del cliente elegido --------------
        self._loans_section = QWidget()
        loans_section_layout = QVBoxLayout(self._loans_section)
        loans_section_layout.setContentsMargins(0, 0, 0, 0)
        loans_section_layout.setSpacing(8)

        back_to_search_button = QPushButton("← Nueva búsqueda")
        back_to_search_button.setFlat(True)
        back_to_search_button.setStyleSheet(theme.flat_button_style())
        back_to_search_button.clicked.connect(self._show_client_search)
        loans_section_layout.addWidget(back_to_search_button)

        self._loans_table = QTableWidget(0, len(_LOANS_TABLE_HEADERS))
        self._loans_table.setHorizontalHeaderLabels(_LOANS_TABLE_HEADERS)
        # La columna "Cuotas" ("Cuota vencida (N)") ya no necesita un ancho
        # fijo de 150px: size_columns la mide contra su propio contenido.
        size_columns(self._loans_table, stretch_column=0)
        self._loans_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._loans_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._loans_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._loans_table.cellDoubleClicked.connect(self._on_row_activated)
        style_table(self._loans_table)
        loans_section_layout.addWidget(self._loans_table, stretch=1)

        layout.addWidget(self._loans_section, stretch=1)
        self._loans_section.hide()

        return page

    # ---- Página de préstamos activos (todos los clientes) -----------------

    def _build_active_loans_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        back_button = QPushButton("← Volver")
        back_button.setFlat(True)
        back_button.setStyleSheet(theme.flat_button_style())
        back_button.clicked.connect(lambda: self._stack.setCurrentIndex(_PAGE_SEARCH))
        layout.addWidget(back_button)

        title = QLabel("Préstamos activos")
        title.setStyleSheet(
            f"font-size: 16px; font-weight: 600; font-family: {theme.HEADING_FONT_FAMILY};"
        )
        layout.addWidget(title)

        self._active_loans_table = QTableWidget(0, len(_ACTIVE_LOANS_TABLE_HEADERS))
        self._active_loans_table.setHorizontalHeaderLabels(_ACTIVE_LOANS_TABLE_HEADERS)
        size_columns(self._active_loans_table, stretch_column=0)
        self._active_loans_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._active_loans_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._active_loans_table.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection
        )
        self._active_loans_table.cellDoubleClicked.connect(
            self._on_active_loan_row_activated
        )
        style_table(self._active_loans_table)
        layout.addWidget(self._active_loans_table, stretch=1)

        return page

    def _show_active_loans_page(self) -> None:
        self._stack.setCurrentIndex(_PAGE_ACTIVE_LOANS)
        self._set_loading(True)
        self._worker = AsyncWorker(
            self._client.list_active_loans,
            self._session.access_token,
            error_translator=_friendly_message,
        )
        self._worker.succeeded.connect(self._on_active_loans_success)
        self._worker.failed.connect(self._on_error)
        self._worker.finished.connect(lambda: self._set_loading(False))
        self._worker.start()

    def _on_active_loans_success(self, response) -> None:
        self._active_loans_table.setRowCount(0)
        for loan in response.loans:
            row = self._active_loans_table.rowCount()
            self._active_loans_table.insertRow(row)
            cuotas_texto, cuotas_color = _cuotas_texto_y_color(loan)
            values = (
                loan.client_name,
                cuotas_texto,
                gs(loan.principal_amount),
                rate_percent(loan.interest_rate),
                str(loan.term_months),
                gs(loan.remaining_balance),
            )
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, (loan.id, loan.client_id))
                if col == 1:
                    item.setForeground(QColor(cuotas_color))
                    if cuotas_texto != "-":
                        bold_font = item.font()
                        bold_font.setBold(True)
                        item.setFont(bold_font)
                self._active_loans_table.setItem(row, col, item)

    def _on_active_loan_row_activated(self, row: int, _column: int) -> None:
        item = self._active_loans_table.item(row, 0)
        if item is None:
            return
        loan_id, client_id = item.data(Qt.ItemDataRole.UserRole)
        self._current_client_id = client_id
        self._stack.setCurrentIndex(_PAGE_DETAIL)
        self._load_detail(loan_id)

    def _show_client_search(self) -> None:
        self._search_title.setText("")
        self._loans_section.hide()
        self._client_search_section.show()

    def _update_picker_buttons(self) -> None:
        has_selection = bool(self._client_results_table.selectedItems())
        self._show_loans_button.setEnabled(has_selection)
        self._view_client_button.setEnabled(has_selection)

    def _selected_client_id(self) -> str | None:
        row = self._client_results_table.currentRow()
        if row < 0:
            return None
        item = self._client_results_table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _on_client_search_submit(self) -> None:
        term = self._client_search_input.text().strip()
        if not term:
            self._toast.show_message("Ingrese un nombre o documento para buscar.")
            return
        self._set_loading(True)
        self._worker = AsyncWorker(
            self._client_service.search_clients,
            self._session.access_token,
            search_term=term,
            page_size=20,
            page_token=0,
            error_translator=_friendly_message,
        )
        self._worker.succeeded.connect(self._on_client_search_success)
        self._worker.failed.connect(self._on_error)
        self._worker.finished.connect(lambda: self._set_loading(False))
        self._worker.start()

    def _on_client_search_success(self, response) -> None:
        self._client_results_table.setRowCount(0)
        for client in response.clients:
            row = self._client_results_table.rowCount()
            self._client_results_table.insertRow(row)
            full_name = f"{client.first_name} {client.last_name}"
            for col, value in enumerate((full_name, client.national_id)):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, client.id)
                self._client_results_table.setItem(row, col, item)
        self._update_picker_buttons()

    def _on_client_row_activated(self, row: int, _column: int) -> None:
        self._client_results_table.selectRow(row)
        self._show_loans_for_selected()

    def _show_loans_for_selected(self) -> None:
        client_id = self._selected_client_id()
        if not client_id:
            return
        display_name = self._client_results_table.item(
            self._client_results_table.currentRow(), 0
        ).text()
        self._show_loans_for(client_id, display_name)

    def _view_client_for_selected(self) -> None:
        client_id = self._selected_client_id()
        if client_id:
            self.view_client_requested.emit(client_id)

    def _show_loans_for(self, client_id: str, display_name: str) -> None:
        self._search_title.setText(f"Préstamos de {display_name}")
        self._client_search_section.hide()
        self._loans_section.show()
        self._run_list(client_id)

    def _run_list(self, client_id: str) -> None:
        self._current_client_id = client_id
        self._set_loading(True)
        self._worker = AsyncWorker(
            self._client.list_client_loans,
            self._session.access_token,
            client_id,
            error_translator=_friendly_message,
        )
        self._worker.succeeded.connect(self._on_list_success)
        self._worker.failed.connect(self._on_error)
        self._worker.finished.connect(lambda: self._set_loading(False))
        self._worker.start()

    def _on_list_success(self, response) -> None:
        self._loans_table.setRowCount(0)
        for loan in response.loans:
            row = self._loans_table.rowCount()
            self._loans_table.insertRow(row)
            cuotas_texto, cuotas_color = _cuotas_texto_y_color(loan)
            values = (
                _ESTADOS_LABEL.get(loan.status, loan.status),
                cuotas_texto,
                gs(loan.principal_amount),
                rate_percent(loan.interest_rate),
                str(loan.term_months),
                gs(loan.remaining_balance),
            )
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, loan.id)
                if col == 0:
                    bg, _fg = _STATUS_STYLE.get(
                        loan.status, (theme.TEXT_MUTED, "white")
                    )
                    item.setForeground(QColor(bg))
                    bold_font = item.font()
                    bold_font.setBold(True)
                    item.setFont(bold_font)
                elif col == 1:
                    item.setForeground(QColor(cuotas_color))
                    if cuotas_texto != "-":
                        bold_font = item.font()
                        bold_font.setBold(True)
                        item.setFont(bold_font)
                self._loans_table.setItem(row, col, item)

    def _on_row_activated(self, row: int, _column: int) -> None:
        item = self._loans_table.item(row, 0)
        if item is None:
            return
        self._load_detail(item.data(Qt.ItemDataRole.UserRole))

    # ---- Página de alta ---------------------------------------------------

    def _build_create_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        back_button = QPushButton("← Volver")
        back_button.setFlat(True)
        back_button.setStyleSheet(theme.flat_button_style())
        back_button.clicked.connect(lambda: self._stack.setCurrentIndex(_PAGE_SEARCH))
        layout.addWidget(back_button)

        card_frame, card_layout = card()
        card_layout.addWidget(section_label("Datos del préstamo"))

        grid = ResponsiveGrid(min_cell_width=220)
        principal_field, self._new_principal = labeled_field(
            "Capital (Gs)", "Ej. 15.000.000", input_cls=CurrencyInput
        )
        term_field, self._new_term = labeled_field("Plazo en meses", "Ej. 12")
        grid.add_widget(principal_field)
        grid.add_widget(term_field)
        card_layout.addWidget(grid)

        rate_field, self._new_rate = labeled_field(
            "Tasa de interés (%)", "Ej. 5 = 5% de interés"
        )
        card_layout.addWidget(rate_field)

        self._rate_hint = QLabel("")
        self._rate_hint.setWordWrap(True)
        self._rate_hint.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px;")
        card_layout.addWidget(self._rate_hint)

        hint = QLabel(
            "La cuota se calcula con el sistema francés (cuota fija mensual), "
            "dividiendo el capital + interés en la cantidad de cuotas elegida. "
            "No debe exceder el 40% del ingreso declarado del cliente (BR-LOAN-002)."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px;")
        card_layout.addWidget(hint)

        submit_button = QPushButton("Solicitar préstamo")
        submit_button.setStyleSheet(theme.accent_button_style(padding="10px"))
        submit_button.clicked.connect(self._on_create_submit)
        card_layout.addWidget(submit_button)

        layout.addWidget(card_frame)
        layout.addStretch()

        return page

    def _show_create_page(self) -> None:
        if not self._current_client_id:
            self._toast.show_message(
                "Seleccione primero un cliente para poder crear un préstamo."
            )
            return
        self._new_principal.clear()
        self._new_term.clear()

        if can_edit_interest_rate(self._session.role):
            self._new_rate.clear()
            self._new_rate.setReadOnly(False)
            self._rate_hint.setText(
                "Como Agente de Créditos/Administrador podés fijar la tasa de "
                "interés del préstamo. Ingresá el número tal cual (5 = 5% de interés)."
            )
        else:
            self._new_rate.setText(fixed_interest_rate_percent())
            self._new_rate.setReadOnly(True)
            self._rate_hint.setText(
                f"Tasa fija para su nivel de usuario: {fixed_interest_rate_percent()}% "
                "de interés. Solo un Agente de Créditos o Administrador puede "
                "modificarla."
            )
        self._stack.setCurrentIndex(_PAGE_CREATE)

    def _on_create_submit(self) -> None:
        principal = self._new_principal.raw_value()
        rate_percent_text = self._new_rate.text().strip()
        term = self._new_term.text().strip()
        if not (principal and rate_percent_text and term):
            self._toast.show_message("Complete capital, tasa y plazo.")
            return
        try:
            term_months = int(term)
        except ValueError:
            self._toast.show_message("El plazo debe ser un número entero de meses.")
            return
        try:
            # The field takes a plain percentage (user types 5 for 5% de
            # interés) -- the server expects the decimal-fraction equivalent.
            rate_decimal = str(Decimal(rate_percent_text.replace(",", ".")) / 100)
        except InvalidOperation:
            self._toast.show_message("La tasa de interés debe ser un número (ej. 5).")
            return

        self._set_loading(True)
        self._worker = AsyncWorker(
            self._client.create_loan,
            self._session.access_token,
            error_translator=_friendly_message,
            client_id=self._current_client_id,
            principal_amount=principal,
            interest_rate=rate_decimal,
            term_months=term_months,
        )
        self._worker.succeeded.connect(self._on_create_success)
        self._worker.failed.connect(self._on_error)
        self._worker.finished.connect(lambda: self._set_loading(False))
        self._worker.start()

    def _on_create_success(self, response) -> None:
        self._toast.show_message(
            "Préstamo solicitado (estado Pendiente). Mostrando el cronograma de cuotas."
        )
        self._run_list(self._current_client_id)
        self._pending_schedule_after_create = True
        self._load_detail(response.loan_id)

    # ---- Página de edición de propuesta (BR-LOAN-004) ----------------------

    def _build_edit_proposal_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        back_button = QPushButton("← Volver")
        back_button.setFlat(True)
        back_button.setStyleSheet(theme.flat_button_style())
        back_button.clicked.connect(lambda: self._stack.setCurrentIndex(_PAGE_DETAIL))
        layout.addWidget(back_button)

        card_frame, card_layout = card()
        card_layout.addWidget(section_label("Editar propuesta de crédito"))

        grid = ResponsiveGrid(min_cell_width=220)
        principal_field, self._edit_principal = labeled_field(
            "Capital (Gs)", "Ej. 15.000.000", input_cls=CurrencyInput
        )
        term_field, self._edit_term = labeled_field("Plazo en meses", "Ej. 12")
        grid.add_widget(principal_field)
        grid.add_widget(term_field)
        card_layout.addWidget(grid)

        due_date_field, self._edit_first_due_date = labeled_field(
            "Primer vencimiento", DISPLAY_DATE_PLACEHOLDER
        )
        card_layout.addWidget(due_date_field)

        hint = QLabel(
            "Solo se puede editar mientras el préstamo esté Pendiente. La cuota "
            "resultante no debe exceder el 40% del ingreso declarado del cliente "
            "(BR-LOAN-002)."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px;")
        card_layout.addWidget(hint)

        submit_button = QPushButton("Guardar propuesta")
        submit_button.setStyleSheet(theme.accent_button_style(padding="10px"))
        submit_button.clicked.connect(self._on_edit_proposal_submit)
        card_layout.addWidget(submit_button)

        layout.addWidget(card_frame)

        # -- Tarjeta de garantía (BR-LOAN-005) --
        guarantee_frame, guarantee_layout = card()
        guarantee_layout.addWidget(section_label("Garantía"))
        guarantee_grid = ResponsiveGrid(min_cell_width=220)
        type_field, self._edit_guarantee_type = labeled_field(
            "Tipo de garantía", "Ej. SOLA FIRMA"
        )
        amount_field, self._edit_guarantee_amount = labeled_field(
            "Monto aplicado (Gs)", "Ej. 6.434.769", input_cls=CurrencyInput
        )
        guarantee_grid.add_widget(type_field)
        guarantee_grid.add_widget(amount_field)
        guarantee_layout.addWidget(guarantee_grid)
        guarantee_hint = QLabel(
            "Ambos campos se completan juntos, o se dejan ambos vacíos (sin "
            "garantía)."
        )
        guarantee_hint.setWordWrap(True)
        guarantee_hint.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px;")
        guarantee_layout.addWidget(guarantee_hint)
        guarantee_submit = QPushButton("Guardar garantía")
        guarantee_submit.setStyleSheet(theme.secondary_button_style())
        guarantee_submit.clicked.connect(self._on_edit_guarantee_submit)
        guarantee_layout.addWidget(guarantee_submit)
        layout.addWidget(guarantee_frame)

        # -- Tarjeta de cargos y seguros (BR-LOAN-006, informativo) --
        charges_frame, charges_layout = card()
        charges_layout.addWidget(section_label("Cargos y seguros"))
        charges_grid = ResponsiveGrid(min_cell_width=220)
        tax_field, self._edit_charge_interest_tax = labeled_field(
            "Impuesto al interés (Gs)", "Ej. 50.000", input_cls=CurrencyInput
        )
        admin_field, self._edit_charge_admin_fee = labeled_field(
            "Gastos administrativos (Gs)", "Ej. 30.000", input_cls=CurrencyInput
        )
        cancel_ins_field, self._edit_charge_cancellation_insurance = labeled_field(
            "Seguro de cancelación de deuda (Gs)", "Ej. 20.000", input_cls=CurrencyInput
        )
        contracted_ins_field, self._edit_charge_contracted_insurance = labeled_field(
            "Seguros contratados (Gs)", "Ej. 10.000", input_cls=CurrencyInput
        )
        charges_grid.add_widget(tax_field)
        charges_grid.add_widget(admin_field)
        charges_grid.add_widget(cancel_ins_field)
        charges_grid.add_widget(contracted_ins_field)
        charges_layout.addWidget(charges_grid)
        charges_hint = QLabel(
            "Cargos informativos: no afectan la cuota mensual ni el tope del "
            "40% de BR-LOAN-002 (BR-LOAN-006)."
        )
        charges_hint.setWordWrap(True)
        charges_hint.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px;")
        charges_layout.addWidget(charges_hint)
        charges_submit = QPushButton("Guardar cargos")
        charges_submit.setStyleSheet(theme.secondary_button_style())
        charges_submit.clicked.connect(self._on_edit_charges_submit)
        charges_layout.addWidget(charges_submit)
        layout.addWidget(charges_frame)

        layout.addStretch()

        return page

    def _show_edit_proposal_page(self) -> None:
        if self._detail_loan is None:
            return
        self._edit_principal.set_amount(self._detail_loan.principal_amount)
        self._edit_term.setText(str(self._detail_loan.term_months))
        self._edit_first_due_date.setText(fecha(self._detail_loan.first_due_date))
        self._edit_guarantee_type.setText(self._detail_loan.guarantee_type)
        self._edit_guarantee_amount.set_amount(self._detail_loan.guarantee_amount)
        self._edit_charge_interest_tax.set_amount(self._detail_loan.charge_interest_tax)
        self._edit_charge_admin_fee.set_amount(self._detail_loan.charge_admin_fee)
        self._edit_charge_cancellation_insurance.set_amount(
            self._detail_loan.charge_cancellation_insurance
        )
        self._edit_charge_contracted_insurance.set_amount(
            self._detail_loan.charge_contracted_insurance
        )
        self._stack.setCurrentIndex(_PAGE_EDIT_PROPOSAL)

    def _on_edit_proposal_submit(self) -> None:
        principal = self._edit_principal.raw_value()
        term = self._edit_term.text().strip()
        first_due_date = self._edit_first_due_date.text().strip()
        if not (principal and term and first_due_date):
            self._toast.show_message("Complete capital, plazo y primer vencimiento.")
            return
        try:
            term_months = int(term)
        except ValueError:
            self._toast.show_message("El plazo debe ser un número entero de meses.")
            return

        self._set_loading(True)
        self._worker = AsyncWorker(
            self._client.update_loan_proposal,
            self._session.access_token,
            error_translator=_friendly_message,
            loan_id=self._selected_loan_id,
            principal_amount=principal,
            term_months=term_months,
            first_due_date=fecha_a_iso(first_due_date),
        )
        self._worker.succeeded.connect(
            lambda _r: self._on_action_success("Propuesta actualizada.")
        )
        self._worker.failed.connect(self._on_error)
        self._worker.finished.connect(lambda: self._set_loading(False))
        self._worker.start()

    def _on_edit_guarantee_submit(self) -> None:
        guarantee_type = self._edit_guarantee_type.text().strip()
        guarantee_amount = self._edit_guarantee_amount.raw_value()
        self._set_loading(True)
        self._worker = AsyncWorker(
            self._client.update_loan_guarantee,
            self._session.access_token,
            error_translator=_friendly_message,
            loan_id=self._selected_loan_id,
            guarantee_type=guarantee_type,
            guarantee_amount=guarantee_amount,
        )
        self._worker.succeeded.connect(
            lambda _r: self._on_action_success("Garantía actualizada.")
        )
        self._worker.failed.connect(self._on_error)
        self._worker.finished.connect(lambda: self._set_loading(False))
        self._worker.start()

    def _on_edit_charges_submit(self) -> None:
        self._set_loading(True)
        self._worker = AsyncWorker(
            self._client.update_loan_charges,
            self._session.access_token,
            error_translator=_friendly_message,
            loan_id=self._selected_loan_id,
            charge_interest_tax=self._edit_charge_interest_tax.raw_value(),
            charge_admin_fee=self._edit_charge_admin_fee.raw_value(),
            charge_cancellation_insurance=(
                self._edit_charge_cancellation_insurance.raw_value()
            ),
            charge_contracted_insurance=(
                self._edit_charge_contracted_insurance.raw_value()
            ),
        )
        self._worker.succeeded.connect(
            lambda _r: self._on_action_success("Cargos actualizados.")
        )
        self._worker.failed.connect(self._on_error)
        self._worker.finished.connect(lambda: self._set_loading(False))
        self._worker.start()

    # ---- Página de detalle -------------------------------------------------

    def _build_detail_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        back_button = QPushButton("← Volver")
        back_button.setFlat(True)
        back_button.setStyleSheet(theme.flat_button_style())
        back_button.clicked.connect(lambda: self._stack.setCurrentIndex(_PAGE_SEARCH))
        layout.addWidget(back_button)

        # -- Tarjeta resumen: título/estado + datos del préstamo --
        summary_frame, summary = card()
        summary.addWidget(section_label("Resumen del préstamo"))
        title_row = QHBoxLayout()
        self._detail_title = QLabel("")
        self._detail_title.setStyleSheet(
            f"font-size: 18px; font-weight: 600; font-family: {theme.HEADING_FONT_FAMILY};"
        )
        title_row.addWidget(self._detail_title, stretch=1)
        self._detail_badge = QLabel("")
        title_row.addWidget(self._detail_badge)
        # Second badge for BR-LOAN-009's payment_status ("Al día" / "Cuota
        # vencida") -- only shown for ACTIVE loans, which the loan status
        # badge alone doesn't distinguish (an ACTIVE loan could be current
        # or behind on payments).
        self._detail_payment_badge = QLabel("")
        title_row.addWidget(self._detail_payment_badge)
        summary.addLayout(title_row)

        self._detail_info = QLabel("")
        self._detail_info.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        self._detail_info.setWordWrap(True)
        summary.addWidget(self._detail_info)
        layout.addWidget(summary_frame)

        # -- Tarjeta de acciones: ciclo de vida + registro de pago --
        actions_frame, actions_card = card()
        actions_card.addWidget(section_label("Acciones y cobro"))
        actions = QHBoxLayout()
        self._edit_proposal_button = QPushButton("Editar propuesta")
        self._edit_proposal_button.setStyleSheet(theme.secondary_button_style())
        self._edit_proposal_button.clicked.connect(self._show_edit_proposal_page)
        actions.addWidget(self._edit_proposal_button)

        self._approve_button = QPushButton("Aprobar")
        self._approve_button.setStyleSheet(theme.secondary_button_style())
        self._approve_button.clicked.connect(self._on_approve)
        actions.addWidget(self._approve_button)

        self._disburse_button = QPushButton("Desembolsar")
        self._disburse_button.setStyleSheet(theme.secondary_button_style())
        self._disburse_button.clicked.connect(self._on_disburse)
        actions.addWidget(self._disburse_button)

        self._default_button = QPushButton("Marcar incumplido")
        self._default_button.setStyleSheet(theme.secondary_button_style())
        self._default_button.clicked.connect(self._on_mark_defaulted)
        actions.addWidget(self._default_button)

        self._schedule_button = QPushButton("Ver cronograma")
        self._schedule_button.setStyleSheet(theme.secondary_button_style())
        self._schedule_button.clicked.connect(self._on_view_schedule)
        actions.addWidget(self._schedule_button)
        actions_card.addLayout(actions)

        payment_row = QHBoxLayout()

        # BR-LOAN-010: el monto ya no se tipea libremente -- se elige la
        # cuota puntual que se está saldando y el monto sale fijo de ahí
        # (el servidor lo recalcula por su cuenta igual, esto es solo para
        # que el usuario vea antes de confirmar). La modificación real del
        # monto de una cuota sigue siendo exclusivamente vía "Ajustar" en
        # el cronograma (UpdateInstallmentAmount), no acá.
        installment_column = QVBoxLayout()
        installment_column.setContentsMargins(0, 0, 0, 0)
        installment_column.setSpacing(4)
        installment_caption = QLabel("Cuota a pagar")
        installment_caption.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 11px; font-weight: 600;"
        )
        installment_column.addWidget(installment_caption)
        self._payment_installment_combo = QComboBox()
        self._payment_installment_combo.currentIndexChanged.connect(
            self._on_payment_installment_changed
        )
        installment_column.addWidget(self._payment_installment_combo)
        payment_row.addLayout(installment_column, stretch=1)

        amount_field, self._payment_amount_display = labeled_field(
            "Monto a registrar (Gs)", input_cls=CurrencyInput
        )
        self._payment_amount_display.setReadOnly(True)
        payment_row.addWidget(amount_field, stretch=1)

        reference_field, self._payment_reference_input = labeled_field(
            "Código/número de transferencia"
        )
        payment_row.addWidget(reference_field, stretch=1)

        # Empty caption-height spacer keeps the button's top edge aligned with
        # the two labeled inputs beside it rather than sitting a row too high.
        button_column = QVBoxLayout()
        button_column.setContentsMargins(0, 0, 0, 0)
        button_column.setSpacing(4)
        button_column.addWidget(QLabel(""))
        self._record_payment_button = QPushButton("Registrar pago")
        self._record_payment_button.setStyleSheet(theme.secondary_button_style())
        self._record_payment_button.clicked.connect(self._on_record_payment)
        button_column.addWidget(self._record_payment_button)
        payment_row.addLayout(button_column)
        actions_card.addLayout(payment_row)

        payment_hint = QLabel(
            "El cobro se realiza por transferencia, descuento directo o descuento "
            "en cuenta específica -- no se maneja efectivo. La referencia es "
            "obligatoria. El monto sale fijo de la cuota elegida; para cambiar el "
            'monto de una cuota use "Ajustar" en el cronograma.'
        )
        payment_hint.setWordWrap(True)
        payment_hint.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px;")
        actions_card.addWidget(payment_hint)
        layout.addWidget(actions_frame)

        # -- Tarjeta de documentos: Liquidación / Pagaré / Contrato --
        docs_frame, docs_card = card()
        docs_card.addWidget(section_label("Documentos"))
        docs_hint = QLabel(
            "El Pagaré y el Contrato son instrumentos legales: se emiten con las "
            "condiciones autorizadas por la entidad (interés compensatorio, mora "
            "y vencimiento anticipado). Verificá los datos del cliente antes de "
            "imprimir y entregar para la firma."
        )
        docs_hint.setWordWrap(True)
        docs_hint.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        docs_card.addWidget(docs_hint)

        self._document_buttons: dict[
            str, tuple[QPushButton, QPushButton, QPushButton]
        ] = {}
        for kind, label in _DOCUMENT_LABELS.items():
            row = QHBoxLayout()
            row.addWidget(QLabel(label), stretch=1)
            download_button = QPushButton("Descargar PDF")
            download_button.setStyleSheet(theme.secondary_button_style())
            download_button.clicked.connect(
                lambda _checked=False, k=kind: self._start_document_export(
                    k, "download"
                )
            )
            row.addWidget(download_button)
            docx_button = QPushButton("Descargar DOCX")
            docx_button.setStyleSheet(theme.secondary_button_style())
            docx_button.clicked.connect(
                lambda _checked=False, k=kind: self._start_document_export(
                    k, "download_docx"
                )
            )
            row.addWidget(docx_button)
            print_button = QPushButton("Imprimir")
            print_button.setStyleSheet(theme.secondary_button_style())
            print_button.clicked.connect(
                lambda _checked=False, k=kind: self._start_document_export(k, "print")
            )
            row.addWidget(print_button)
            docs_card.addLayout(row)
            self._document_buttons[kind] = (download_button, docx_button, print_button)
        layout.addWidget(docs_frame)

        layout.addStretch()
        return page

    def _load_detail(self, loan_id: str) -> None:
        self._set_loading(True)
        self._worker = AsyncWorker(
            self._client.get_loan_by_id,
            self._session.access_token,
            loan_id,
            error_translator=_friendly_message,
        )
        self._worker.succeeded.connect(self._on_detail_loaded)
        self._worker.failed.connect(self._on_error)
        self._worker.finished.connect(lambda: self._set_loading(False))
        self._worker.start()

    def _on_detail_loaded(self, loan) -> None:
        self._selected_loan_id = loan.id
        self._detail_loan = loan
        self._detail_title.setText(f"Préstamo {loan.id[:8]}…")
        _apply_status_badge(self._detail_badge, loan.status)
        if loan.payment_status:
            cuotas_texto, cuotas_color = _cuotas_texto_y_color(loan)
            self._detail_payment_badge.setText(cuotas_texto)
            self._detail_payment_badge.setStyleSheet(
                f"background-color: {cuotas_color}; color: white; border-radius: 10px; "
                "padding: 3px 10px; font-size: 12px; font-weight: 600;"
            )
            self._detail_payment_badge.show()
        else:
            self._detail_payment_badge.hide()
        garantia = (
            f"{loan.guarantee_type} ({gs(loan.guarantee_amount)})"
            if loan.guarantee_type
            else "-"
        )
        self._detail_info.setText(
            f"Capital: {gs(loan.principal_amount)}  ·  "
            f"Tasa de interés: {rate_percent(loan.interest_rate)}  ·  "
            f"Plazo: {loan.term_months} meses  ·  "
            f"Primer vencimiento: {fecha(loan.first_due_date)}\n"
            f"Total pagado: {gs(loan.total_paid)}  ·  "
            f"Saldo restante: {gs(loan.remaining_balance)}\n"
            f"Garantía: {garantia}  ·  Total cargos: {gs(loan.total_charges)}  ·  "
            f"Total con cargos: {gs(loan.total_credit_with_charges)}"
        )

        puede_pagare_contrato = loan.status in ("APPROVED", "ACTIVE", "PAID")
        total_pagado = Decimal(loan.total_paid) if loan.total_paid else Decimal("0")
        puede_liquidacion = loan.status == "PAID" or total_pagado > 0
        for kind, (
            download_button,
            docx_button,
            print_button,
        ) in self._document_buttons.items():
            if kind == "liquidacion":
                enabled = puede_liquidacion
            elif kind == "comprobante":
                # BR-LOAN-011: solo hay comprobante para un pago concreto, y
                # solo se conserva el de esta sesión (ver _last_payment).
                enabled = (
                    self._last_payment is not None
                    and self._last_payment_loan_id == loan.id
                )
            else:
                enabled = puede_pagare_contrato
            # "cronograma" se entrega al cliente una vez que el préstamo tiene
            # un cronograma confirmado (mismo criterio que pagaré/contrato:
            # APPROVED/ACTIVE/PAID), no ya en PENDING, aunque el cronograma
            # sea calculable desde antes -- entregarlo antes de la aprobación
            # induciría a pensar que los términos ya están cerrados.
            download_button.setEnabled(enabled)
            docx_button.setEnabled(enabled)
            print_button.setEnabled(enabled)

        role = self._session.role
        self._edit_proposal_button.setEnabled(loan.status == "PENDING")
        self._approve_button.setEnabled(
            loan.status == "PENDING" and role_at_least(role, "CREDIT_ANALYST")
        )
        self._disburse_button.setEnabled(
            loan.status == "APPROVED" and role_at_least(role, "MANAGER")
        )
        self._default_button.setEnabled(
            loan.status == "ACTIVE" and role_at_least(role, "CREDIT_ANALYST")
        )
        can_pay = loan.status == "ACTIVE" and role_at_least(role, "CASHIER")
        self._record_payment_button.setEnabled(can_pay)
        self._payment_installment_combo.setEnabled(can_pay)
        self._payment_reference_input.setEnabled(can_pay)
        if can_pay:
            self._load_pending_installments()
        else:
            self._payment_installment_combo.clear()
            self._payment_amount_display.clear()

        self._stack.setCurrentIndex(_PAGE_DETAIL)

        if self._pending_schedule_after_create:
            self._pending_schedule_after_create = False
            self._on_view_schedule()

    def _run_loan_action(self, worker_fn, *args, on_success, **kwargs) -> None:
        self._set_loading(True)
        self._worker = AsyncWorker(
            worker_fn,
            self._session.access_token,
            *args,
            error_translator=_friendly_message,
            **kwargs,
        )
        self._worker.succeeded.connect(on_success)
        self._worker.failed.connect(self._on_error)
        self._worker.finished.connect(lambda: self._set_loading(False))
        self._worker.start()

    def _on_approve(self) -> None:
        self._run_loan_action(
            self._client.approve_loan,
            self._selected_loan_id,
            on_success=lambda _r: self._on_action_success("Préstamo aprobado."),
        )

    def _on_disburse(self) -> None:
        self._run_loan_action(
            self._client.disburse_loan,
            self._selected_loan_id,
            on_success=lambda _r: self._on_action_success("Préstamo desembolsado."),
        )

    def _on_mark_defaulted(self) -> None:
        confirm = QMessageBox.question(
            self, "Confirmar", "¿Marcar este préstamo como incumplido?"
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._run_loan_action(
            self._client.mark_defaulted,
            self._selected_loan_id,
            on_success=lambda _r: self._on_action_success(
                "Préstamo marcado como incumplido."
            ),
        )

    def _load_pending_installments(self) -> None:
        """Puebla "Cuota a pagar" con las cuotas todavía no cubiertas
        (BR-LOAN-010) -- fetch separado del detalle porque necesita el
        cronograma completo (GetAmortizationSchedule), no solo lo que ya
        trae GetLoanById."""
        self._payment_installment_combo.clear()
        self._payment_amount_display.clear()
        self._installments_worker = AsyncWorker(
            self._client.get_amortization_schedule,
            self._session.access_token,
            self._selected_loan_id,
            error_translator=_friendly_message,
        )
        self._installments_worker.succeeded.connect(
            self._on_pending_installments_loaded
        )
        self._installments_worker.failed.connect(self._on_error)
        self._installments_worker.start()

    def _on_pending_installments_loaded(self, response) -> None:
        self._payment_installment_combo.clear()
        for installment in response.installments:
            if installment.is_paid:
                continue
            label = (
                f"Cuota {installment.installment_number} · "
                f"Vence {fecha(installment.due_date)} · {gs(installment.amount_due)}"
            )
            self._payment_installment_combo.addItem(
                label, (installment.installment_number, installment.amount_due)
            )
        has_pending = self._payment_installment_combo.count() > 0
        self._record_payment_button.setEnabled(
            has_pending and self._record_payment_button.isEnabled()
        )
        self._on_payment_installment_changed(
            self._payment_installment_combo.currentIndex()
        )

    def _on_payment_installment_changed(self, index: int) -> None:
        data = self._payment_installment_combo.itemData(index)
        if data is None:
            self._payment_amount_display.clear()
            return
        _numero, monto_pendiente = data
        self._payment_amount_display.set_amount(monto_pendiente)

    def _on_record_payment(self) -> None:
        index = self._payment_installment_combo.currentIndex()
        data = self._payment_installment_combo.itemData(index)
        if data is None:
            self._toast.show_message("No hay cuotas pendientes para registrar un pago.")
            return
        numero_cuota, _monto = data
        reference = self._payment_reference_input.text().strip()
        if not reference:
            self._toast.show_message(
                "Ingrese el código o número de transferencia del pago."
            )
            return
        self._run_loan_action(
            self._client.record_payment,
            self._selected_loan_id,
            reference,
            installment_number=numero_cuota,
            on_success=self._on_payment_recorded,
        )

    def _on_payment_recorded(self, response) -> None:
        self._payment_reference_input.clear()
        self._last_payment = response
        self._last_payment_loan_id = self._selected_loan_id
        cuotas = documents.cuotas_cubiertas_texto(
            response.covered_installments, response.total_installments
        )
        # _on_action_success recarga el detalle, que es lo que re-habilita la
        # fila del comprobante en la tarjeta de Documentos.
        self._on_action_success(
            f"Pago registrado — {cuotas}. Puede descargar el comprobante en "
            '"Documentos".'
        )

    def _on_action_success(self, message: str) -> None:
        self._toast.show_message(message)
        self._load_detail(self._selected_loan_id)

    # ---- Página de cronograma (sistema francés) ---------------------------

    def _build_schedule_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        back_button = QPushButton("← Volver")
        back_button.setFlat(True)
        back_button.setStyleSheet(theme.flat_button_style())
        back_button.clicked.connect(lambda: self._stack.setCurrentIndex(_PAGE_DETAIL))
        layout.addWidget(back_button)

        title = QLabel("Cronograma de amortización (sistema francés)")
        title.setStyleSheet(
            f"font-size: 16px; font-weight: 600; font-family: {theme.HEADING_FONT_FAMILY};"
        )
        # Can get clipped at the app's minimum window width otherwise --
        # word wrap lets it flow to a second line instead.
        title.setWordWrap(True)
        layout.addWidget(title)

        # Tiras de estadísticas -- evoca el isotipo de "gráfico de barras
        # ascendente" del doc de branding como resumen visual, sin depender de
        # una librería de gráficos (no hay ninguna en este proyecto).
        stats_row = QHBoxLayout()
        paid_frame, self._schedule_paid_value = stat_tile("Total pagado (Gs)")
        stats_row.addWidget(paid_frame)
        remaining_frame, self._schedule_remaining_value = stat_tile(
            "Saldo restante (Gs)"
        )
        stats_row.addWidget(remaining_frame)
        layout.addLayout(stats_row)

        self._schedule_table = QTableWidget(0, len(_SCHEDULE_TABLE_HEADERS))
        self._schedule_table.setHorizontalHeaderLabels(_SCHEDULE_TABLE_HEADERS)
        size_columns(self._schedule_table, stretch_column=2)
        # La última columna lleva el botón "Ajustar" vía setCellWidget, y
        # ResizeToContents mide el delegate del ítem, no el widget de la
        # celda -- por eso quedaba a un ancho que recortaba el botón a "ust".
        # Se fija a partir del sizeHint real del mismo botón que se va a
        # insertar, en vez de un número mágico que dependa de la fuente.
        probe = QPushButton("Ajustar")
        probe.setStyleSheet(theme.flat_button_style())
        self._schedule_table.horizontalHeader().setSectionResizeMode(
            _SCHEDULE_COL_ADJUST, QHeaderView.ResizeMode.Fixed
        )
        self._schedule_table.setColumnWidth(
            _SCHEDULE_COL_ADJUST, probe.sizeHint().width() + 32
        )
        self._schedule_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        style_table(self._schedule_table)
        layout.addWidget(self._schedule_table, stretch=1)

        return page

    def _on_view_schedule(self) -> None:
        if self._selected_loan_id is None:
            return
        self._set_loading(True)
        self._worker = AsyncWorker(
            self._client.get_amortization_schedule,
            self._session.access_token,
            self._selected_loan_id,
            error_translator=_friendly_message,
        )
        self._worker.succeeded.connect(self._on_schedule_loaded)
        self._worker.failed.connect(self._on_error)
        self._worker.finished.connect(lambda: self._set_loading(False))
        self._worker.start()

    def _connect_adjust_button(self, button: QPushButton, installment) -> None:
        """Extracted out of _on_schedule_loaded's loop so the connect() call
        doesn't need a long lambda default-argument list on one line (each
        call gets its own numero/monto locals, so no shared-loop-variable
        closure bug -- same binding trick as the lambda it replaces, just
        via a fresh function scope instead of default arguments)."""
        numero = installment.installment_number
        monto = installment.payment_amount
        button.clicked.connect(
            lambda _checked=False: self._on_adjust_installment(numero, monto)
        )

    def _on_schedule_loaded(self, response) -> None:
        self._schedule_table.setRowCount(0)
        is_active = (
            self._detail_loan is not None and self._detail_loan.status == "ACTIVE"
        )
        can_adjust = is_active and can_edit_installment_amount(self._session.role)
        for installment in response.installments:
            row = self._schedule_table.rowCount()
            self._schedule_table.insertRow(row)
            monto_label = gs(installment.payment_amount)
            if installment.is_adjusted:
                monto_label += " (ajustada)"
            values = (
                str(installment.installment_number),
                fecha(installment.due_date),
                monto_label,
                gs(installment.principal_portion),
                gs(installment.interest_portion),
                gs(installment.remaining_balance),
            )
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if installment.is_adjusted:
                    item.setBackground(QColor(theme.ACCENT).lighter(160))
                self._schedule_table.setItem(row, col, item)

            is_last_installment = (
                self._detail_loan is not None
                and installment.installment_number >= self._detail_loan.term_months
            )
            if can_adjust and not is_last_installment:
                button = QPushButton("Ajustar")
                button.setCursor(Qt.CursorShape.PointingHandCursor)
                button.setStyleSheet(theme.flat_button_style())
                self._connect_adjust_button(button, installment)
                self._schedule_table.setCellWidget(row, _SCHEDULE_COL_ADJUST, button)

        self._schedule_paid_value.setText(gs(response.total_paid))
        self._schedule_remaining_value.setText(gs(response.remaining_balance))
        self._stack.setCurrentIndex(_PAGE_SCHEDULE)

    def _on_adjust_installment(
        self, installment_number: int, current_amount: str
    ) -> None:
        current_whole = str(int(Decimal(current_amount)))
        text, ok = QInputDialog.getText(
            self,
            "Ajustar cuota",
            f"Nuevo monto para la cuota {installment_number} (Gs):",
            text=current_whole,
        )
        if not ok:
            return
        text = text.strip().replace(".", "")
        try:
            monto = Decimal(text)
        except Exception:
            self._toast.show_message("Ingrese un monto numérico válido.")
            return
        if monto <= 0:
            self._toast.show_message("El monto debe ser mayor a cero.")
            return

        self._set_loading(True)
        self._worker = AsyncWorker(
            self._client.update_installment_amount,
            self._session.access_token,
            error_translator=_friendly_message,
            loan_id=self._selected_loan_id,
            installment_number=installment_number,
            adjusted_amount=str(monto),
        )
        self._worker.succeeded.connect(lambda _r: self._on_installment_adjusted())
        self._worker.failed.connect(self._on_error)
        self._worker.finished.connect(lambda: self._set_loading(False))
        self._worker.start()

    def _on_installment_adjusted(self) -> None:
        self._toast.show_message("Cuota ajustada.")
        self._on_view_schedule()

    # ---- Documentos (Liquidación / Pagaré / Contrato) ----------------------

    def _start_document_export(self, kind: str, action: str) -> None:
        if self._detail_loan is None:
            return
        self._pending_document = (kind, action)
        self._set_loading(True)
        self._worker = AsyncWorker(
            self._client_service.get_client_by_id,
            self._session.access_token,
            self._detail_loan.client_id,
            error_translator=_friendly_message,
        )
        self._worker.succeeded.connect(self._on_document_client_loaded)
        self._worker.failed.connect(self._on_error)
        self._worker.finished.connect(lambda: self._set_loading(False))
        self._worker.start()

    def _on_document_client_loaded(self, client) -> None:
        kind, _action = self._pending_document
        if kind in ("liquidacion", "cronograma"):
            self._set_loading(True)
            self._worker = AsyncWorker(
                self._client.get_amortization_schedule,
                self._session.access_token,
                self._detail_loan.id,
                error_translator=_friendly_message,
            )
            self._worker.succeeded.connect(
                lambda schedule: self._render_document(client, schedule)
            )
            self._worker.failed.connect(self._on_error)
            self._worker.finished.connect(lambda: self._set_loading(False))
            self._worker.start()
        else:
            self._render_document(client, None)

    def _render_document(self, client, schedule) -> None:
        kind, action = self._pending_document
        loan = self._detail_loan

        if action == "download_docx":
            self._save_document_docx(kind, loan, client, schedule)
            return

        if kind == "liquidacion":
            html = documents.liquidacion_html(loan, client, schedule)
            default_name = f"liquidacion_{loan.id[:8]}.pdf"
        elif kind == "pagare":
            html = documents.pagare_html(loan, client)
            default_name = f"pagare_{loan.id[:8]}.pdf"
        elif kind == "cronograma":
            html = documents.cronograma_html(loan, client, schedule)
            default_name = f"cronograma_{loan.id[:8]}.pdf"
        elif kind == "comprobante":
            html = documents.comprobante_pago_html(loan, client, self._last_payment)
            default_name = f"comprobante_{loan.id[:8]}.pdf"
        else:
            html = documents.contrato_html(loan, client)
            default_name = f"contrato_{loan.id[:8]}.pdf"

        document = QTextDocument()
        document.setHtml(html)

        if action == "download":
            path, _filter = QFileDialog.getSaveFileName(
                self, "Guardar documento", default_name, "PDF (*.pdf)"
            )
            if not path:
                return
            printer = QPrinter(QPrinter.PrinterMode.HighResolution)
            printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            printer.setOutputFileName(path)
            try:
                document.print_(printer)
            except OSError as exc:
                self._toast.show_message(_friendly_file_error(exc))
                return
            self._toast.show_message("Documento guardado.")
        else:
            printer = QPrinter(QPrinter.PrinterMode.HighResolution)
            dialog = QPrintDialog(printer, self)
            if dialog.exec() == QPrintDialog.DialogCode.Accepted:
                try:
                    document.print_(printer)
                except OSError as exc:
                    self._toast.show_message(_friendly_file_error(exc))

    def _save_document_docx(self, kind: str, loan, client, schedule) -> None:
        if kind == "liquidacion":
            docx_document = documents_docx.liquidacion_docx(loan, client, schedule)
            default_name = f"liquidacion_{loan.id[:8]}.docx"
        elif kind == "pagare":
            docx_document = documents_docx.pagare_docx(loan, client)
            default_name = f"pagare_{loan.id[:8]}.docx"
        elif kind == "cronograma":
            docx_document = documents_docx.cronograma_docx(loan, client, schedule)
            default_name = f"cronograma_{loan.id[:8]}.docx"
        elif kind == "comprobante":
            docx_document = documents_docx.comprobante_pago_docx(
                loan, client, self._last_payment
            )
            default_name = f"comprobante_{loan.id[:8]}.docx"
        else:
            docx_document = documents_docx.contrato_docx(loan, client)
            default_name = f"contrato_{loan.id[:8]}.docx"

        path, _filter = QFileDialog.getSaveFileName(
            self, "Guardar documento", default_name, "Word (*.docx)"
        )
        if not path:
            return
        try:
            docx_document.save(path)
        except OSError as exc:
            self._toast.show_message(_friendly_file_error(exc))
            return
        self._toast.show_message("Documento guardado.")

    # ---- Helpers comunes -----------------------------------------------

    def _set_loading(self, loading: bool) -> None:
        # Toggling only visibility leaves the indeterminate busy-animation
        # timer running while hidden, which can leave a stale animation
        # frame (a light streak) baked into the parent's backing store when
        # the QStackedWidget switches pages right after. Parking the range
        # at (0, 1) while hidden stops that timer.
        if loading:
            self._progress.setRange(0, 0)
        else:
            self._progress.setRange(0, 1)
            self._progress.setValue(0)
        self._progress.setVisible(loading)

    def _on_error(self, message: str) -> None:
        self._toast.show_message(message)
