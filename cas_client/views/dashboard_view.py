from datetime import date, datetime, timedelta

import grpc
from PySide6.QtCore import Qt
from PySide6.QtGui import QPageLayout, QTextDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrinter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cas_client import documents, documents_docx, theme
from cas_client.formatting import (
    DISPLAY_DATE_FORMAT,
    DISPLAY_DATE_PLACEHOLDER,
    es_fecha_valida,
    fecha,
    fecha_a_iso,
    gs,
)
from cas_client.grpc_client import ApiError, DashboardServiceClient
from cas_client.rbac_ui import (
    can_view_payment_status_report,
    can_view_period_report,
    tier_label,
)
from cas_client.session import Session
from cas_client.widgets.async_worker import AsyncWorker
from cas_client.widgets.base_view import BaseView
from cas_client.widgets.card import (
    card,
    labeled_combo,
    labeled_field,
    section_label,
    stat_tile,
)
from cas_client.widgets.responsive_grid import ResponsiveGrid
from cas_client.widgets.table import set_empty_message, size_columns, style_table
from cas_client.widgets.toast import Toast

_LOAN_STATUS_TILES = (
    ("pending_loans_count", "Pendientes de aprobación", "PENDING", "PE"),
    ("approved_loans_count", "Aprobados (sin desembolsar)", "APPROVED", "AP"),
    ("active_loans_count", "Activos", "ACTIVE", "AC"),
    ("paid_loans_count", "Pagados", "PAID", "PG"),
    ("defaulted_loans_count", "Incumplidos", "DEFAULTED", "IN"),
    # LoanStatusEnum.EXPIRED. La etiqueta pasó de "Caducados" a "Rechazados"
    # por decisión de producto -- el estado del servidor y el nombre del campo
    # (expired_loans_count) no cambiaron. Ver la misma nota en loans_view.py.
    ("expired_loans_count", "Rechazados", "EXPIRED", "RE"),
)

_UN_DIA = timedelta(days=1)

_REPORT_TABLE_HEADERS = ("Sección", "Concepto", "Valor")

# BR-DASH-003. El valor que viaja es el bool `only_overdue` de la request; la
# etiqueta es lo único que ve el operador. Se guarda como itemData para no
# depender del texto, misma convención que el combo de roles de users_view.py.
_PAYMENT_STATUS_FILTERS = (
    ("Todos los clientes con cartera viva", False),
    ("Solo clientes con atraso", True),
)


def _greeting() -> str:
    hour = datetime.now().hour
    if 5 <= hour < 12:
        return "Buenos días"
    if 12 <= hour < 19:
        return "Buenas tardes"
    return "Buenas noches"


def _format_time(dt: datetime) -> str:
    hour12 = dt.hour % 12 or 12
    suffix = "a. m." if dt.hour < 12 else "p. m."
    return f"{hour12}:{dt.minute:02d} {suffix}"


def _friendly_message(exc: Exception) -> str:
    if isinstance(exc, ApiError):
        if exc.code == grpc.StatusCode.PERMISSION_DENIED:
            return "No tiene permisos para ver las estadísticas del dashboard."
        if exc.code == grpc.StatusCode.UNAVAILABLE:
            return "No se pudo conectar con el servidor."
        return "No se pudieron cargar las estadísticas. Intente nuevamente."
    return f"No se pudo conectar con el servidor: {exc}"


def _friendly_report_message(exc: Exception) -> str:
    if isinstance(exc, ApiError):
        if exc.code == grpc.StatusCode.PERMISSION_DENIED:
            return "No tiene permisos para generar reportes de período."
        if exc.code == grpc.StatusCode.INVALID_ARGUMENT:
            return (
                "Revise las fechas del período: use el formato "
                f"{DISPLAY_DATE_PLACEHOLDER} y que la fecha final no sea "
                "anterior a la inicial."
            )
        if exc.code == grpc.StatusCode.UNAVAILABLE:
            return "No se pudo conectar con el servidor."
        return "No se pudo generar el reporte. Intente nuevamente."
    return f"No se pudo conectar con el servidor: {exc}"


def _friendly_payment_status_message(exc: Exception) -> str:
    if isinstance(exc, ApiError):
        if exc.code == grpc.StatusCode.PERMISSION_DENIED:
            return "No tiene permisos para ver el estado de pago de los clientes."
        if exc.code == grpc.StatusCode.UNAVAILABLE:
            return "No se pudo conectar con el servidor."
        return "No se pudo generar el reporte de estado de pago. Intente nuevamente."
    return f"No se pudo conectar con el servidor: {exc}"


def _rango_mes(anchor: date) -> tuple[date, date]:
    """Primer y último día del mes en que cae `anchor`."""
    inicio = anchor.replace(day=1)
    if inicio.month == 12:
        fin = inicio.replace(year=inicio.year + 1, month=1) - _UN_DIA
    else:
        fin = inicio.replace(month=inicio.month + 1) - _UN_DIA
    return inicio, fin


def _preset_mes_actual() -> tuple[date, date]:
    return _rango_mes(date.today())


def _preset_mes_anterior() -> tuple[date, date]:
    primero_de_este_mes = date.today().replace(day=1)
    return _rango_mes(primero_de_este_mes - _UN_DIA)


def _preset_anio_actual() -> tuple[date, date]:
    hoy = date.today()
    return date(hoy.year, 1, 1), date(hoy.year, 12, 31)


# Atajos para los cierres más habituales. Deliberadamente no incluye "últimos
# 30 días": un cierre de período es por definición un rango calendario
# (mes/año), no una ventana móvil.
_PRESETS_PERIODO = (
    ("Mes actual", _preset_mes_actual),
    ("Mes anterior", _preset_mes_anterior),
    ("Año actual", _preset_anio_actual),
)


class DashboardView(BaseView):
    """Pantalla de inicio: saludo + estadísticas agregadas de clientes/préstamos.

    Reemplaza el antiguo placeholder de bienvenida -- ver GetDashboardStats en
    dashboard_service.proto (no había ninguna RPC de agregados hasta ahora).
    """

    def __init__(
        self,
        client: DashboardServiceClient,
        session: Session,
        parent: QWidget | None = None,
    ):
        super().__init__(parent=parent)
        self._client = client
        self._session = session
        self._worker: AsyncWorker | None = None
        self._report_worker: AsyncWorker | None = None
        self._report = None  # último GetPeriodReportResponse recibido
        self._payment_status_worker: AsyncWorker | None = None
        # último GetClientPaymentStatusReportResponse recibido (BR-DASH-003)
        self._payment_status = None
        # Bumped on every _refresh_stats() call so a stale worker's callbacks
        # (a previous refresh still in flight when the user tabs back in,
        # see showEvent()) can tell they've been superseded and no-op instead
        # of overwriting a newer response or hiding the newer request's
        # progress bar -- ES-006 §3.5.
        self._refresh_generation = 0

        header_row = QHBoxLayout()
        header_row.setSpacing(12)

        heading_column = QVBoxLayout()
        heading_column.setSpacing(2)
        self._heading_label = QLabel(f"{_greeting()} — Panel")
        self._heading_label.setStyleSheet(
            f"font-size: 22px; font-weight: 600; color: {theme.PRIMARY}; "
            f"font-family: {theme.HEADING_FONT_FAMILY};"
        )
        # Non-wrapping QLabels can get silently compressed below their own
        # text width by the layout under tight space (a recurring gotcha in
        # this codebase -- see login_view.py's wordmark for the full
        # explanation); Minimum pins sizeHint() as a hard floor instead.
        self._heading_label.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred
        )
        heading_column.addWidget(self._heading_label)
        self._updated_label = QLabel("")
        self._updated_label.setStyleSheet(
            f"font-size: 12px; color: {theme.TEXT_MUTED};"
        )
        heading_column.addWidget(self._updated_label)
        header_row.addLayout(heading_column, stretch=1)

        refresh_button = QPushButton("Actualizar")
        refresh_button.setCursor(Qt.CursorShape.PointingHandCursor)
        refresh_button.setStyleSheet(theme.secondary_button_style())
        refresh_button.clicked.connect(self._refresh_stats)
        header_row.addWidget(refresh_button, alignment=Qt.AlignmentFlag.AlignTop)
        self.content_layout.addLayout(header_row)

        self._welcome_label = QLabel("")
        self._welcome_label.setStyleSheet(
            f"font-size: 14px; color: {theme.TEXT_MUTED};"
        )
        # A long username + role ("Sesión iniciada como ... · Nivel:
        # Administrador") can legitimately exceed the available width at the
        # app's minimum window size -- word wrap lets it flow to a second
        # line instead of getting clipped.
        self._welcome_label.setWordWrap(True)
        self.content_layout.addWidget(self._welcome_label)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(4)
        self._progress.hide()
        self.content_layout.addWidget(self._progress)

        self._error_label = QLabel("")
        self._error_label.setStyleSheet(f"color: {theme.ERROR}; font-size: 12px;")
        self._error_label.setWordWrap(True)
        self._error_label.hide()
        self.content_layout.addWidget(self._error_label)

        self.content_layout.addWidget(section_label("Clientes"))
        self._clients_grid = ResponsiveGrid(min_cell_width=220)
        self.content_layout.addWidget(self._clients_grid)

        self.content_layout.addWidget(section_label("Préstamos por estado"))
        # Wider than the other grids -- these captions ("Aprobados (sin
        # desembolsar)", etc.) are the longest on the dashboard, so a wider
        # minimum column keeps _ElidingLabel's "…" truncation rare in
        # normal window sizes instead of just safe when it happens.
        self._loans_grid = ResponsiveGrid(min_cell_width=260)
        self.content_layout.addWidget(self._loans_grid)

        self.content_layout.addWidget(section_label("Cartera"))
        self._portfolio_grid = ResponsiveGrid(min_cell_width=260)
        self.content_layout.addWidget(self._portfolio_grid)

        self._reports_section_label = section_label("Reportes de cierre de período")
        self.content_layout.addWidget(self._reports_section_label)
        self._reports_card = self._build_reports_card()
        self.content_layout.addWidget(self._reports_card)
        # Oculto hasta que set_user() sepa el rol -- GetPeriodReport es
        # MANAGER+ server-side (rbac.py), así que ofrecerlo a un rol Estándar
        # solo produciría un PERMISSION_DENIED. Misma convención de
        # ocultar-en-vez-de-deshabilitar que el ítem "Usuarios" del sidebar.
        self._set_reports_visible(False)

        self._payment_status_section_label = section_label(
            "Estado de pago de los clientes"
        )
        self.content_layout.addWidget(self._payment_status_section_label)
        self._payment_status_card = self._build_payment_status_card()
        self.content_layout.addWidget(self._payment_status_card)
        # Oculto hasta que set_user() sepa el rol, igual que la tarjeta de
        # cierre de período -- GetClientPaymentStatusReport es
        # CREDIT_ANALYST_AND_ABOVE server-side (rbac.py).
        self._set_payment_status_visible(False)

        self.content_layout.addStretch()

        self._toast = Toast(self)
        self._rebuild_tiles(stats=None)

    # ---- Reportes de cierre de período (BR-DASH-002) --------------------

    def _build_reports_card(self) -> QWidget:
        frame, layout = card()

        intro = QLabel(
            "Genere el resumen de un período cerrado (mes, trimestre o año) "
            "para imprimirlo o archivarlo."
        )
        intro.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px;")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        presets_row = QHBoxLayout()
        presets_row.setSpacing(8)
        for label, resolver in _PRESETS_PERIODO:
            button = QPushButton(label)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(theme.secondary_button_style(padding="6px 12px"))
            # `resolver` se liga como default para que cada botón capture el
            # suyo y no el último del loop (closure de Qt, ver CLAUDE.md).
            button.clicked.connect(
                lambda _checked=False, r=resolver: self._apply_preset(r)
            )
            presets_row.addWidget(button)
        presets_row.addStretch()
        layout.addLayout(presets_row)

        range_grid = ResponsiveGrid(min_cell_width=200)
        desde_field, self._report_start = labeled_field(
            "Desde", DISPLAY_DATE_PLACEHOLDER
        )
        range_grid.add_widget(desde_field)
        hasta_field, self._report_end = labeled_field("Hasta", DISPLAY_DATE_PLACEHOLDER)
        range_grid.add_widget(hasta_field)
        layout.addWidget(range_grid)

        actions_row = QHBoxLayout()
        actions_row.setSpacing(8)
        generate_button = QPushButton("Generar reporte")
        generate_button.setCursor(Qt.CursorShape.PointingHandCursor)
        generate_button.setStyleSheet(theme.accent_button_style())
        generate_button.clicked.connect(self._on_generate_report)
        actions_row.addWidget(generate_button)

        self._report_export_buttons: list[QPushButton] = []
        for label, handler in (
            ("Descargar PDF", self._on_report_download_pdf),
            ("Descargar DOCX", self._on_report_download_docx),
            ("Imprimir", self._on_report_print),
        ):
            button = QPushButton(label)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(theme.secondary_button_style())
            button.clicked.connect(handler)
            button.setEnabled(False)  # hasta que haya un reporte cargado
            actions_row.addWidget(button)
            self._report_export_buttons.append(button)
        actions_row.addStretch()
        layout.addLayout(actions_row)

        self._report_progress = QProgressBar()
        self._report_progress.setRange(0, 0)
        self._report_progress.setTextVisible(False)
        self._report_progress.setFixedHeight(4)
        self._report_progress.hide()
        layout.addWidget(self._report_progress)

        self._report_caption = QLabel("")
        self._report_caption.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 12px;"
        )
        self._report_caption.setWordWrap(True)
        layout.addWidget(self._report_caption)

        self._report_table = QTableWidget(0, len(_REPORT_TABLE_HEADERS))
        self._report_table.setHorizontalHeaderLabels(_REPORT_TABLE_HEADERS)
        # "Concepto" absorbe el ancho sobrante; "Sección"/"Valor" se miden
        # contra su propio contenido -- si no, "Movimiento del período" se
        # elide a "Movimient..." y el reporte queda ilegible en su tabla.
        size_columns(self._report_table, stretch_column=1)
        self._report_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._report_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        # La tabla vive dentro del scroll de la página (wrap_scrollable en
        # main_window.py). Sin una altura propia, un QTableWidget dentro de un
        # QScrollArea se queda en su altura mínima y scrollea aparte -- el
        # "scroll dentro de scroll" que confunde al usuario. La altura real se
        # fija en _fit_report_table_height() una vez que hay filas, medida
        # sobre el layout ya calculado en vez de estimada con un alto de fila
        # supuesto (theme.py's fonts aren't bundled, so row metrics vary por
        # máquina -- misma advertencia que card.py's _ElidingLabel).
        self._report_table.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._report_table.setVisible(False)
        style_table(self._report_table)
        layout.addWidget(self._report_table)

        return frame

    def _set_reports_visible(self, visible: bool) -> None:
        self._reports_section_label.setVisible(visible)
        self._reports_card.setVisible(visible)

    def _apply_preset(self, resolver) -> None:
        inicio, fin = resolver()
        # DISPLAY_DATE_FORMAT y no un "%d/%m/%Y" literal: el formato de
        # presentación lo define formatting.py, y un atajo que escribiera en
        # otro formato le entregaría al campo algo que el propio placeholder
        # no pide.
        self._report_start.setText(inicio.strftime(DISPLAY_DATE_FORMAT))
        self._report_end.setText(fin.strftime(DISPLAY_DATE_FORMAT))

    def _on_generate_report(self) -> None:
        if not self._session.access_token:
            return
        inicio = self._report_start.text().strip()
        fin = self._report_end.text().strip()
        self._report_start.set_error(not inicio)
        self._report_end.set_error(not fin)
        if not (inicio and fin):
            self._toast.show_message(
                "Indique el rango del período (desde y hasta), o use un atajo."
            )
            return

        # El servidor también valida, pero su mensaje nombra el formato de
        # cable ("AAAA-MM-DD"); acá se avisa en el formato que el campo pide.
        malas = False
        for campo, valor in ((self._report_start, inicio), (self._report_end, fin)):
            malo = not es_fecha_valida(valor)
            campo.set_error(malo)
            malas = malas or malo
        if malas:
            self._toast.show_message(
                "Revise las fechas del período: use el formato "
                f"{DISPLAY_DATE_PLACEHOLDER}."
            )
            return

        self._report_progress.setRange(0, 0)
        self._report_progress.show()
        self._report_worker = AsyncWorker(
            self._client.get_period_report,
            self._session.access_token,
            fecha_a_iso(inicio),
            fecha_a_iso(fin),
            error_translator=_friendly_report_message,
        )
        self._report_worker.succeeded.connect(self._on_report_loaded)
        self._report_worker.failed.connect(self._on_report_failed)
        self._report_worker.finished.connect(self._hide_report_progress)
        self._report_worker.start()

    def _hide_report_progress(self) -> None:
        # Mismo motivo que _hide_progress(): parkear el rango detiene el
        # timer de la animación indeterminada mientras está oculta.
        self._report_progress.hide()
        self._report_progress.setRange(0, 1)
        self._report_progress.setValue(0)

    def _on_report_loaded(self, report) -> None:
        self._report = report
        # El servidor devuelve el rango que realmente aplicó -- se muestra ese,
        # no lo que quedó tipeado en los campos.
        self._report_start.setText(fecha(report.start_date))
        self._report_end.setText(fecha(report.end_date))
        self._report_caption.setText(
            f"Período del {fecha(report.start_date)} al {fecha(report.end_date)}."
        )

        filas = documents._filas_reporte(report)
        self._report_table.setRowCount(0)
        for seccion, concepto, valor in filas:
            row = self._report_table.rowCount()
            self._report_table.insertRow(row)
            for col, texto in enumerate((seccion, concepto, valor)):
                item = QTableWidgetItem(texto)
                if col == 2:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self._report_table.setItem(row, col, item)
        self._report_table.setVisible(True)
        self._fit_report_table_height()
        for button in self._report_export_buttons:
            button.setEnabled(True)

    def _fit_report_table_height(self) -> None:
        """Fija la altura de la tabla a la suma real de sus filas para que
        muestre el reporte completo de una, sin barra de scroll propia."""
        alto = self._report_table.horizontalHeader().height()
        for row in range(self._report_table.rowCount()):
            alto += self._report_table.rowHeight(row)
        alto += 2 * self._report_table.frameWidth()
        self._report_table.setFixedHeight(alto)

    def _on_report_failed(self, message: str) -> None:
        self._toast.show_message(message)

    def _report_default_name(self, extension: str) -> str:
        return (
            f"reporte_{self._report.start_date}_a_{self._report.end_date}.{extension}"
        )

    def _on_report_download_pdf(self) -> None:
        if self._report is None:
            return
        document = QTextDocument()
        document.setHtml(
            documents.reporte_periodo_html(self._report, self._session.username or "")
        )
        path, _filter = QFileDialog.getSaveFileName(
            self, "Guardar reporte", self._report_default_name("pdf"), "PDF (*.pdf)"
        )
        if not path:
            return
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(path)
        try:
            document.print_(printer)
        except OSError as exc:
            self._toast.show_message(documents.friendly_file_error(exc))
            return
        self._toast.show_message("Reporte guardado.")

    def _on_report_download_docx(self) -> None:
        if self._report is None:
            return
        path, _filter = QFileDialog.getSaveFileName(
            self, "Guardar reporte", self._report_default_name("docx"), "Word (*.docx)"
        )
        if not path:
            return
        try:
            documents_docx.reporte_periodo_docx(
                self._report, self._session.username or ""
            ).save(path)
        except OSError as exc:
            self._toast.show_message(documents.friendly_file_error(exc))
            return
        self._toast.show_message("Reporte guardado.")

    def _on_report_print(self) -> None:
        if self._report is None:
            return
        document = QTextDocument()
        document.setHtml(
            documents.reporte_periodo_html(self._report, self._session.username or "")
        )
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        dialog = QPrintDialog(printer, self)
        if dialog.exec() == QPrintDialog.DialogCode.Accepted:
            try:
                document.print_(printer)
            except OSError as exc:
                self._toast.show_message(documents.friendly_file_error(exc))

    # ---- Estado de pago de los clientes (BR-DASH-003) -------------------

    def _build_payment_status_card(self) -> QWidget:
        frame, layout = card()

        intro = QLabel(
            "Consulte quién está al día y quién debe: una fila por cliente con "
            "cartera viva, con su saldo, lo vencido y su próximo vencimiento."
        )
        intro.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px;")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        filter_grid = ResponsiveGrid(min_cell_width=260)
        filter_field, self._payment_status_filter = labeled_combo("Alcance")
        for label, only_overdue in _PAYMENT_STATUS_FILTERS:
            self._payment_status_filter.addItem(label, only_overdue)
        filter_grid.add_widget(filter_field)
        layout.addWidget(filter_grid)

        actions_row = ResponsiveGrid(min_cell_width=150)
        generate_button = QPushButton("Generar reporte")
        generate_button.setCursor(Qt.CursorShape.PointingHandCursor)
        generate_button.setStyleSheet(theme.accent_button_style())
        generate_button.clicked.connect(self._on_generate_payment_status)
        actions_row.add_widget(generate_button)

        self._payment_status_export_buttons: list[QPushButton] = []
        for label, handler in (
            ("Descargar PDF", self._on_payment_status_download_pdf),
            ("Descargar DOCX", self._on_payment_status_download_docx),
            ("Imprimir", self._on_payment_status_print),
        ):
            button = QPushButton(label)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(theme.secondary_button_style())
            button.clicked.connect(handler)
            button.setEnabled(False)  # hasta que haya un reporte cargado
            actions_row.add_widget(button)
            self._payment_status_export_buttons.append(button)
        layout.addWidget(actions_row)

        self._payment_status_progress = QProgressBar()
        self._payment_status_progress.setRange(0, 0)
        self._payment_status_progress.setTextVisible(False)
        self._payment_status_progress.setFixedHeight(4)
        self._payment_status_progress.hide()
        layout.addWidget(self._payment_status_progress)

        self._payment_status_caption = QLabel("")
        self._payment_status_caption.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 12px;"
        )
        self._payment_status_caption.setWordWrap(True)
        layout.addWidget(self._payment_status_caption)

        self._payment_status_table = QTableWidget(
            0, len(documents.ESTADO_PAGOS_COLUMNAS)
        )
        self._payment_status_table.setHorizontalHeaderLabels(
            list(documents.ESTADO_PAGOS_COLUMNAS)
        )
        # "Cliente" absorbe el sobrante: es la columna de ancho más variable
        # (nombre + apellido) y la que el operador lee primero.
        size_columns(self._payment_status_table, stretch_column=0)
        self._payment_status_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._payment_status_table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        self._payment_status_table.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        style_table(self._payment_status_table)
        set_empty_message(
            self._payment_status_table,
            'Sin datos todavía. Elija el alcance y pulse "Generar reporte".',
        )
        layout.addWidget(self._payment_status_table)

        return frame

    def _set_payment_status_visible(self, visible: bool) -> None:
        self._payment_status_section_label.setVisible(visible)
        self._payment_status_card.setVisible(visible)

    def _on_generate_payment_status(self) -> None:
        if not self._session.access_token:
            return
        only_overdue = bool(self._payment_status_filter.currentData())
        self._payment_status_progress.setRange(0, 0)
        self._payment_status_progress.show()
        self._payment_status_worker = AsyncWorker(
            self._client.get_client_payment_status_report,
            self._session.access_token,
            only_overdue,
            error_translator=_friendly_payment_status_message,
        )
        self._payment_status_worker.succeeded.connect(self._on_payment_status_loaded)
        self._payment_status_worker.failed.connect(self._on_payment_status_failed)
        self._payment_status_worker.finished.connect(self._hide_payment_status_progress)
        self._payment_status_worker.start()

    def _hide_payment_status_progress(self) -> None:
        # Mismo motivo que _hide_progress(): parkear el rango detiene el timer
        # de la animación indeterminada mientras está oculta.
        self._payment_status_progress.hide()
        self._payment_status_progress.setRange(0, 1)
        self._payment_status_progress.setValue(0)

    def _on_payment_status_loaded(self, report) -> None:
        self._payment_status = report
        alcance = (
            "solo clientes con atraso"
            if report.only_overdue
            else "todos los clientes con cartera viva"
        )
        self._payment_status_caption.setText(
            f"{report.clients_count} cliente(s) informado(s) ({alcance}), "
            f"{report.overdue_clients_count} con atraso · Saldo pendiente "
            f"{gs(report.total_outstanding)} · Vencido {gs(report.total_overdue)}."
        )
        # Mismas filas que el PDF/DOCX -- ver documents._filas_estado_pagos.
        filas = documents._filas_estado_pagos(report)
        self._payment_status_table.setRowCount(0)
        for tupla in filas:
            row = self._payment_status_table.rowCount()
            self._payment_status_table.insertRow(row)
            for col, texto in enumerate(tupla):
                item = QTableWidgetItem(texto)
                if col in documents.ESTADO_PAGOS_COLUMNAS_NUMERICAS:
                    item.setTextAlignment(
                        Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    )
                self._payment_status_table.setItem(row, col, item)
        if not filas:
            set_empty_message(
                self._payment_status_table,
                "Ningún cliente cumple el alcance elegido.",
            )
        self._fit_payment_status_table_height()
        for button in self._payment_status_export_buttons:
            button.setEnabled(True)

    def _fit_payment_status_table_height(self) -> None:
        """Misma razón que _fit_report_table_height(): la tabla vive dentro del
        scroll de la página, así que se le fija la altura real de sus filas en
        vez de dejar que scrollee por su cuenta."""
        alto = self._payment_status_table.horizontalHeader().height()
        for row in range(self._payment_status_table.rowCount()):
            alto += self._payment_status_table.rowHeight(row)
        alto += 2 * self._payment_status_table.frameWidth()
        # Una tabla sin filas quedaría en la altura del encabezado y taparía el
        # mensaje de vacío que set_empty_message() dibuja encima.
        self._payment_status_table.setFixedHeight(max(alto, 120))

    def _on_payment_status_failed(self, message: str) -> None:
        self._toast.show_message(message)

    def _payment_status_printer(self) -> QPrinter:
        """Impresora en horizontal: la tabla del reporte tiene 8 columnas y en
        vertical cada celda se parte en varias líneas. Es el único documento
        de la app que lo necesita -- el resto son tablas de 2 o 3 columnas.
        El .docx hace lo mismo por su lado (documents_docx._apaisar)."""
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setPageOrientation(QPageLayout.Orientation.Landscape)
        return printer

    def _payment_status_default_name(self, extension: str) -> str:
        dia = self._payment_status.generated_at.ToDatetime().date().isoformat()
        return f"estado_de_pago_{dia}.{extension}"

    def _on_payment_status_download_pdf(self) -> None:
        if self._payment_status is None:
            return
        document = QTextDocument()
        document.setHtml(
            documents.reporte_estado_pagos_html(
                self._payment_status, self._session.username or ""
            )
        )
        path, _filter = QFileDialog.getSaveFileName(
            self,
            "Guardar reporte",
            self._payment_status_default_name("pdf"),
            "PDF (*.pdf)",
        )
        if not path:
            return
        printer = self._payment_status_printer()
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(path)
        try:
            document.print_(printer)
        except OSError as exc:
            self._toast.show_message(documents.friendly_file_error(exc))
            return
        self._toast.show_message("Reporte guardado.")

    def _on_payment_status_download_docx(self) -> None:
        if self._payment_status is None:
            return
        path, _filter = QFileDialog.getSaveFileName(
            self,
            "Guardar reporte",
            self._payment_status_default_name("docx"),
            "Word (*.docx)",
        )
        if not path:
            return
        try:
            documents_docx.reporte_estado_pagos_docx(
                self._payment_status, self._session.username or ""
            ).save(path)
        except OSError as exc:
            self._toast.show_message(documents.friendly_file_error(exc))
            return
        self._toast.show_message("Reporte guardado.")

    def _on_payment_status_print(self) -> None:
        if self._payment_status is None:
            return
        document = QTextDocument()
        document.setHtml(
            documents.reporte_estado_pagos_html(
                self._payment_status, self._session.username or ""
            )
        )
        printer = self._payment_status_printer()
        dialog = QPrintDialog(printer, self)
        if dialog.exec() == QPrintDialog.DialogCode.Accepted:
            try:
                document.print_(printer)
            except OSError as exc:
                self._toast.show_message(documents.friendly_file_error(exc))

    # ---- Estado de sesión ----------------------------------------------

    def set_user(self, username: str, role: str) -> None:
        self._welcome_label.setText(
            f"Sesión iniciada como {username} · Nivel: {tier_label(role)}"
        )
        self._set_reports_visible(can_view_period_report(role))
        self._set_payment_status_visible(can_view_payment_status_report(role))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.update()
        if self._session.access_token:
            self._refresh_stats()

    def _refresh_stats(self) -> None:
        if not self._session.access_token:
            return
        self._refresh_generation += 1
        generation = self._refresh_generation
        self._heading_label.setText(f"{_greeting()} — Panel")
        self._error_label.hide()
        self._show_progress()
        self._worker = AsyncWorker(
            self._client.get_dashboard_stats,
            self._session.access_token,
            error_translator=_friendly_message,
        )
        self._worker.succeeded.connect(
            lambda stats: self._on_stats_loaded(stats, generation)
        )
        self._worker.failed.connect(
            lambda message: self._on_stats_failed(message, generation)
        )
        self._worker.finished.connect(lambda: self._hide_progress_for(generation))
        self._worker.start()

    def _hide_progress_for(self, generation: int) -> None:
        if generation == self._refresh_generation:
            self._hide_progress()

    def _show_progress(self) -> None:
        self._progress.setRange(0, 0)
        self._progress.show()

    def _hide_progress(self) -> None:
        # Toggling only visibility leaves the indeterminate busy-animation
        # timer running while hidden, which can leave a stale animation
        # frame (a light streak) baked into the parent's backing store when
        # the QStackedWidget switches pages right after. Parking the range
        # at (0, 1) while hidden stops that timer.
        self._progress.hide()
        self._progress.setRange(0, 1)
        self._progress.setValue(0)

    def _rebuild_tiles(self, stats) -> None:
        """(Re)builds every stat tile from scratch rather than mutating an
        existing QLabel's text in place.

        Repeatedly calling setText() on long-lived tile widgets (the
        original approach here) turned out not to be reliably safe on this
        app's actual Windows target: after several refresh/navigate cycles,
        stray fragments of *previous* renders (old glyph edges, an old
        badge outline) would bleed through as faint ghosting -- a stale
        backing-store repaint issue, not the caption-wrap overlap this
        module used to have. update()/repaint() calls here previously
        tried to paper over that and weren't reliable enough (still
        reproduced after ~8 refresh cycles). Tearing down and recreating
        the tile widgets each time sidesteps the whole bug class: a freshly
        constructed QLabel has no prior paint history to bleed through.
        """
        self._clients_grid.clear()
        clients_total_frame, _ = stat_tile(
            "Total de clientes",
            accent_color=theme.PRIMARY,
            icon_text="T",
            value_text=str(stats.total_clients_count) if stats else "—",
        )
        self._clients_grid.add_widget(clients_total_frame)
        clients_active_frame, _ = stat_tile(
            "Clientes activos",
            accent_color=theme.SUCCESS,
            icon_text="A",
            value_text=str(stats.active_clients_count) if stats else "—",
        )
        self._clients_grid.add_widget(clients_active_frame)

        self._loans_grid.clear()
        for field, label, status_key, icon_text in _LOAN_STATUS_TILES:
            accent_color, _fg = theme.LOAN_STATUS_COLORS[status_key]
            frame, _ = stat_tile(
                label,
                accent_color=accent_color,
                icon_text=icon_text,
                value_text=str(getattr(stats, field)) if stats else "—",
            )
            self._loans_grid.add_widget(frame)

        self._portfolio_grid.clear()
        disbursed_frame, _ = stat_tile(
            "Cartera desembolsada (Gs)",
            accent_color=theme.PRIMARY,
            icon_text="₲",
            value_text=gs(stats.total_disbursed) if stats else "—",
        )
        self._portfolio_grid.add_widget(disbursed_frame)
        outstanding_frame, _ = stat_tile(
            "Saldo pendiente de cobro (Gs)",
            accent_color=theme.ACCENT,
            icon_text="₲",
            value_text=gs(stats.total_outstanding_balance) if stats else "—",
        )
        self._portfolio_grid.add_widget(outstanding_frame)
        # BR-DASH-001. Subconjunto del saldo pendiente de arriba: solo lo ya
        # vencido e impago -- lo que la empresa debería recibir cuando los
        # deudores se pongan al día con sus cuotas atrasadas. Rojo, porque a
        # diferencia del saldo pendiente esto sí es un problema a la vista.
        overdue_frame, _ = stat_tile(
            "Monto total de mora (Gs)",
            accent_color=theme.ERROR,
            icon_text="₲",
            value_text=gs(stats.total_overdue_amount) if stats else "—",
        )
        self._portfolio_grid.add_widget(overdue_frame)
        overdue_count_frame, _ = stat_tile(
            "Préstamos en mora",
            accent_color=theme.ERROR,
            icon_text="MO",
            value_text=str(stats.overdue_loans_count) if stats else "—",
        )
        self._portfolio_grid.add_widget(overdue_count_frame)

    def _on_stats_loaded(self, stats, generation: int) -> None:
        if generation != self._refresh_generation:
            return  # respuesta de un refresh superado por uno más nuevo
        self._rebuild_tiles(stats)
        self._updated_label.setText(f"Actualizado a las {_format_time(datetime.now())}")

    def _on_stats_failed(self, message: str, generation: int) -> None:
        if generation != self._refresh_generation:
            return
        self._error_label.setText(message)
        self._error_label.show()
