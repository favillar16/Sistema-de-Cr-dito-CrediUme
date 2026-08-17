"""Pantalla de Caja (BR-CAJA-001..004): apertura del turno, movimientos de
efectivo del día y cierre con arqueo, más el historial de arqueos.

Es la pantalla principal del rol Cajero. La vista nunca calcula el monto
esperado por su cuenta: siempre muestra el `expected_amount` que devuelve el
servidor, porque es ese número -- y no uno recalculado acá -- el que se usa
para el arqueo del cierre.
"""

import grpc
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from cas_client import theme
from cas_client.formatting import (
    DISPLAY_DATE_PLACEHOLDER,
    fecha_a_iso,
    fecha_hora,
    gs,
)
from cas_client.grpc_client import ApiError, CashServiceClient
from cas_client.rbac_ui import can_supervise_cash_sessions
from cas_client.session import Session
from cas_client.widgets.async_worker import AsyncWorker
from cas_client.widgets.base_view import BaseView
from cas_client.widgets.card import card, labeled_field, section_label, stat_tile
from cas_client.widgets.currency_input import CurrencyInput
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
        session: Session,
        parent: QWidget | None = None,
    ):
        super().__init__("Caja", parent=parent)
        self._client = client
        self._session = session
        self._worker: AsyncWorker | None = None
        self._history_worker: AsyncWorker | None = None
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
