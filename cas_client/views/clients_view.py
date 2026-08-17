import grpc
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
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

from cas_client import theme
from cas_client.formatting import DISPLAY_DATE_PLACEHOLDER, fecha, fecha_a_iso
from cas_client.grpc_client import ApiError, ClientServiceClient
from cas_client.rbac_ui import can_originate_credit, role_at_least
from cas_client.session import Session
from cas_client.widgets.async_worker import AsyncWorker
from cas_client.widgets.base_view import BaseView
from cas_client.widgets.card import card, labeled_field, section_label
from cas_client.widgets.currency_input import CurrencyInput
from cas_client.widgets.form_input import FormInput
from cas_client.widgets.responsive_grid import ResponsiveGrid
from cas_client.widgets.scroll_area import wrap_scrollable
from cas_client.widgets.table import size_columns, style_table
from cas_client.widgets.toast import Toast

_PAGE_LIST, _PAGE_CREATE, _PAGE_DETAIL = range(3)

_TABLE_HEADERS = ("Nombre", "Documento", "Email", "Teléfono", "Estado")


def _friendly_message(
    exc: Exception, *, not_found: str = "Cliente no encontrado."
) -> str:
    if isinstance(exc, ApiError):
        if exc.code == grpc.StatusCode.NOT_FOUND:
            return not_found
        if exc.code == grpc.StatusCode.ALREADY_EXISTS:
            return "Ya existe un cliente con ese documento o email."
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


def _reference_cards() -> tuple[
    tuple,
    tuple,
    tuple,
]:
    """Builds the 3 reference cards (BR-CLI-005) shared by the create and
    detail forms: (frame, name_field, relationship_field, phone_field) for
    each personal reference, and (frame, employer, position, phone, seniority)
    for the employment reference."""
    ref1_frame, ref1 = card()
    ref1.addWidget(section_label("Referencia personal 1"))
    name_field, ref1_name = labeled_field("Nombre completo", required=True)
    ref1.addWidget(name_field)
    ref1_grid = ResponsiveGrid(min_cell_width=220)
    rel_field, ref1_relationship = labeled_field("Parentesco/relación", required=True)
    ref1_grid.add_widget(rel_field)
    phone_field, ref1_phone = labeled_field("Teléfono", required=True)
    ref1_grid.add_widget(phone_field)
    ref1.addWidget(ref1_grid)

    ref2_frame, ref2 = card()
    ref2.addWidget(section_label("Referencia personal 2"))
    name_field2, ref2_name = labeled_field("Nombre completo", required=True)
    ref2.addWidget(name_field2)
    ref2_grid = ResponsiveGrid(min_cell_width=220)
    rel_field2, ref2_relationship = labeled_field("Parentesco/relación", required=True)
    ref2_grid.add_widget(rel_field2)
    phone_field2, ref2_phone = labeled_field("Teléfono", required=True)
    ref2_grid.add_widget(phone_field2)
    ref2.addWidget(ref2_grid)

    job_frame, job = card()
    job.addWidget(section_label("Referencia laboral"))
    job_grid = ResponsiveGrid(min_cell_width=220)
    employer_field, job_employer = labeled_field("Empleador", required=True)
    job_grid.add_widget(employer_field)
    position_field, job_position = labeled_field("Cargo", required=True)
    job_grid.add_widget(position_field)
    job_phone_field, job_phone = labeled_field("Teléfono", required=True)
    job_grid.add_widget(job_phone_field)
    seniority_field, job_seniority = labeled_field(
        "Antigüedad", "Ej. 3 años", required=True
    )
    job_grid.add_widget(seniority_field)
    job.addWidget(job_grid)

    return (
        (ref1_frame, ref1_name, ref1_relationship, ref1_phone),
        (ref2_frame, ref2_name, ref2_relationship, ref2_phone),
        (job_frame, job_employer, job_position, job_phone, job_seniority),
    )


def _extended_profile_card() -> tuple:
    """Builds the "Perfil extendido" card (BR-CLI-007, todos los campos
    opcionales), compartida por el formulario de alta y el de detalle."""
    frame, layout = card()
    layout.addWidget(section_label("Perfil extendido (opcional)"))
    grid = ResponsiveGrid(min_cell_width=220)
    expiry_field, expiry = labeled_field(
        "Vencimiento de cédula", DISPLAY_DATE_PLACEHOLDER
    )
    grid.add_widget(expiry_field)
    marital_field, marital = labeled_field("Estado civil")
    grid.add_widget(marital_field)
    education_field, education = labeled_field("Nivel de estudios")
    grid.add_widget(education_field)
    occupation_field, occupation = labeled_field("Ocupación")
    grid.add_widget(occupation_field)
    neighborhood_field, neighborhood = labeled_field("Barrio")
    grid.add_widget(neighborhood_field)
    risk_field, risk = labeled_field("Calificación de riesgo", "Ej. Muy bajo")
    grid.add_widget(risk_field)
    layout.addWidget(grid)
    sector_field, sector = labeled_field("Sector económico")
    layout.addWidget(sector_field)
    return frame, expiry, marital, education, occupation, neighborhood, risk, sector


class ClientsView(BaseView):
    """Alta, búsqueda y gestión de clientes (KYC) -- specs/clients/README."""

    view_loans_requested = Signal(str, str)  # client_id, nombre_completo

    def __init__(
        self,
        client: ClientServiceClient,
        session: Session,
        parent: QWidget | None = None,
    ):
        super().__init__(title="Clientes", parent=parent)
        self._client = client
        self._session = session
        self._worker: AsyncWorker | None = None
        self._next_page_token = 0
        self._last_search_term = ""
        self._selected_client_id: str | None = None

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setTextVisible(False)
        self._progress.setFixedHeight(4)
        self._progress.hide()
        self.content_layout.addWidget(self._progress)

        self._stack = QStackedWidget()
        self.content_layout.addWidget(self._stack)

        self._stack.addWidget(self._build_list_page())
        self._stack.addWidget(wrap_scrollable(self._build_create_page()))
        self._stack.addWidget(wrap_scrollable(self._build_detail_page()))

        self._toast = Toast(self)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._stack.currentIndex() == _PAGE_LIST and self._table.rowCount() == 0:
            self._run_search(reset=True)

    # ---- Página de lista / búsqueda ----------------------------------

    def _build_list_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        toolbar = QHBoxLayout()
        self._search_input = FormInput("Buscar por nombre, documento o teléfono")
        self._search_input.returnPressed.connect(lambda: self._run_search(reset=True))
        toolbar.addWidget(self._search_input, stretch=1)

        search_button = QPushButton("Buscar")
        search_button.setStyleSheet(theme.secondary_button_style())
        search_button.clicked.connect(lambda: self._run_search(reset=True))
        toolbar.addWidget(search_button)

        # BR-CAJA-005: el alta de clientes es CREDIT_ANALYST_AND_ABOVE en
        # rbac.py desde que el rol Cajero volvió a usarse. Se oculta en vez de
        # deshabilitarse, misma convención que el ítem "Usuarios" del sidebar.
        # La visibilidad real se fija en apply_role(), que MainWindow llama al
        # iniciar sesión -- acá la vista todavía no conoce el rol.
        self._new_button = QPushButton("Nuevo cliente")
        self._new_button.setStyleSheet(theme.accent_button_style())
        self._new_button.clicked.connect(self._show_create_page)
        toolbar.addWidget(self._new_button)

        layout.addLayout(toolbar)

        self._table = QTableWidget(0, len(_TABLE_HEADERS))
        self._table.setHorizontalHeaderLabels(_TABLE_HEADERS)
        size_columns(self._table, stretch_column=0)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.cellDoubleClicked.connect(self._on_row_activated)
        style_table(self._table)
        layout.addWidget(self._table, stretch=1)

        self._next_page_button = QPushButton("Siguiente página")
        self._next_page_button.setStyleSheet(theme.secondary_button_style())
        self._next_page_button.setEnabled(False)
        self._next_page_button.clicked.connect(self._load_next_page)
        layout.addWidget(self._next_page_button)

        return page

    def _run_search(self, reset: bool) -> None:
        if reset:
            self._next_page_token = 0
            self._table.setRowCount(0)
            self._last_search_term = self._search_input.text().strip()

        self._set_loading(True)
        self._worker = AsyncWorker(
            self._client.search_clients,
            self._session.access_token,
            search_term=self._last_search_term,
            page_size=20,
            page_token=self._next_page_token,
            error_translator=_friendly_message,
        )
        self._worker.succeeded.connect(self._on_search_success)
        self._worker.failed.connect(self._on_error)
        self._worker.finished.connect(lambda: self._set_loading(False))
        self._worker.start()

    def _load_next_page(self) -> None:
        self._run_search(reset=False)

    def _on_search_success(self, response) -> None:
        for client in response.clients:
            row = self._table.rowCount()
            self._table.insertRow(row)
            full_name = f"{client.first_name} {client.last_name}"
            estado = "Activo" if client.is_active else "Inactivo"
            for col, value in enumerate(
                (
                    full_name,
                    client.national_id,
                    client.email,
                    client.phone_number,
                    estado,
                )
            ):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, client.id)
                self._table.setItem(row, col, item)

        self._next_page_token = response.next_page_token
        self._next_page_button.setEnabled(self._next_page_token > 0)

    def _on_row_activated(self, row: int, _column: int) -> None:
        item = self._table.item(row, 0)
        if item is None:
            return
        client_id = item.data(Qt.ItemDataRole.UserRole)
        self._load_detail(client_id)

    # ---- Página de alta -----------------------------------------------

    def _build_create_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        back_button = QPushButton("← Volver")
        back_button.setFlat(True)
        back_button.setStyleSheet(theme.flat_button_style())
        back_button.clicked.connect(lambda: self._stack.setCurrentIndex(_PAGE_LIST))
        layout.addWidget(back_button)

        personal_frame, personal = card()
        personal.addWidget(section_label("Datos personales"))
        grid = ResponsiveGrid(min_cell_width=220)

        first_name_field, self._new_first_name = labeled_field("Nombre", required=True)
        grid.add_widget(first_name_field)
        last_name_field, self._new_last_name = labeled_field("Apellido", required=True)
        grid.add_widget(last_name_field)

        national_id_field, self._new_national_id = labeled_field(
            "Documento de identidad", required=True
        )
        grid.add_widget(national_id_field)
        dob_field, self._new_dob = labeled_field(
            "Fecha de nacimiento", DISPLAY_DATE_PLACEHOLDER, required=True
        )
        grid.add_widget(dob_field)

        email_field, self._new_email = labeled_field("Email", required=True)
        grid.add_widget(email_field)
        phone_field, self._new_phone = labeled_field("Teléfono", required=True)
        grid.add_widget(phone_field)
        personal.addWidget(grid)

        address_field, self._new_address = labeled_field("Dirección", required=True)
        personal.addWidget(address_field)
        income_field, self._new_income = labeled_field(
            "Ingreso mensual declarado (Gs, opcional)",
            "Ej. 4.500.000",
            input_cls=CurrencyInput,
        )
        personal.addWidget(income_field)
        source_of_funds_field, self._new_source_of_funds = labeled_field(
            "Origen de fondos", "Ej. Salario, Negocio propio", required=True
        )
        personal.addWidget(source_of_funds_field)

        layout.addWidget(personal_frame)

        (
            extended_frame,
            self._new_national_id_expiry_date,
            self._new_marital_status,
            self._new_education_level,
            self._new_occupation,
            self._new_neighborhood,
            self._new_risk_rating,
            self._new_economic_sector,
        ) = _extended_profile_card()
        layout.addWidget(extended_frame)

        (
            (
                ref1_frame,
                self._new_ref1_name,
                self._new_ref1_relationship,
                self._new_ref1_phone,
            ),
            (
                ref2_frame,
                self._new_ref2_name,
                self._new_ref2_relationship,
                self._new_ref2_phone,
            ),
            (
                job_frame,
                self._new_ref_job_employer,
                self._new_ref_job_position,
                self._new_ref_job_phone,
                self._new_ref_job_seniority,
            ),
        ) = _reference_cards()
        layout.addWidget(ref1_frame)
        layout.addWidget(ref2_frame)
        layout.addWidget(job_frame)

        submit_button = QPushButton("Registrar cliente")
        submit_button.setStyleSheet(theme.accent_button_style(padding="10px"))
        submit_button.clicked.connect(self._on_create_submit)
        layout.addWidget(submit_button)
        layout.addStretch()

        return page

    def _show_create_page(self) -> None:
        for widget in (
            self._new_first_name,
            self._new_last_name,
            self._new_national_id,
            self._new_email,
            self._new_phone,
            self._new_dob,
            self._new_address,
            self._new_income,
            self._new_source_of_funds,
            self._new_national_id_expiry_date,
            self._new_marital_status,
            self._new_education_level,
            self._new_occupation,
            self._new_neighborhood,
            self._new_risk_rating,
            self._new_economic_sector,
            self._new_ref1_name,
            self._new_ref1_relationship,
            self._new_ref1_phone,
            self._new_ref2_name,
            self._new_ref2_relationship,
            self._new_ref2_phone,
            self._new_ref_job_employer,
            self._new_ref_job_position,
            self._new_ref_job_phone,
            self._new_ref_job_seniority,
        ):
            widget.clear()
            widget.set_error(False)
        self._stack.setCurrentIndex(_PAGE_CREATE)

    def _on_create_submit(self) -> None:
        required_fields = (
            (self._new_first_name, "first_name"),
            (self._new_last_name, "last_name"),
            (self._new_national_id, "national_id"),
            (self._new_email, "email"),
            (self._new_phone, "phone_number"),
            (self._new_dob, "date_of_birth"),
            (self._new_address, "address"),
            (self._new_source_of_funds, "source_of_funds"),
            (self._new_ref1_name, "personal_reference_1_name"),
            (self._new_ref1_relationship, "personal_reference_1_relationship"),
            (self._new_ref1_phone, "personal_reference_1_phone"),
            (self._new_ref2_name, "personal_reference_2_name"),
            (self._new_ref2_relationship, "personal_reference_2_relationship"),
            (self._new_ref2_phone, "personal_reference_2_phone"),
            (self._new_ref_job_employer, "employment_reference_employer"),
            (self._new_ref_job_position, "employment_reference_position"),
            (self._new_ref_job_phone, "employment_reference_phone"),
            (self._new_ref_job_seniority, "employment_reference_seniority"),
        )
        fields = {}
        has_blank = False
        for widget, field_name in required_fields:
            value = widget.text().strip()
            widget.set_error(not value)
            has_blank = has_blank or not value
            fields[field_name] = value
        if has_blank:
            self._toast.show_message("Complete los campos marcados en rojo.")
            return
        # El usuario tipea DD/MM/AAAA; el contrato gRPC es ISO (ver formatting.py).
        fields["date_of_birth"] = fecha_a_iso(fields["date_of_birth"])

        income = self._new_income.raw_value()
        if income:
            fields["declared_monthly_income"] = income

        fields.update(
            national_id_expiry_date=fecha_a_iso(
                self._new_national_id_expiry_date.text()
            ),
            marital_status=self._new_marital_status.text().strip(),
            education_level=self._new_education_level.text().strip(),
            occupation=self._new_occupation.text().strip(),
            neighborhood=self._new_neighborhood.text().strip(),
            risk_rating=self._new_risk_rating.text().strip(),
            economic_sector=self._new_economic_sector.text().strip(),
        )

        self._set_loading(True)
        self._worker = AsyncWorker(
            self._client.create_client,
            self._session.access_token,
            error_translator=_friendly_message,
            **fields,
        )
        self._worker.succeeded.connect(self._on_create_success)
        self._worker.failed.connect(self._on_error)
        self._worker.finished.connect(lambda: self._set_loading(False))
        self._worker.start()

    def _on_create_success(self, _response) -> None:
        self._toast.show_message("Cliente registrado con éxito.")
        self._run_search(reset=True)
        self._stack.setCurrentIndex(_PAGE_LIST)

    # ---- Página de detalle ---------------------------------------------

    def _build_detail_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        back_button = QPushButton("← Volver")
        back_button.setFlat(True)
        back_button.setStyleSheet(theme.flat_button_style())
        back_button.clicked.connect(lambda: self._stack.setCurrentIndex(_PAGE_LIST))
        layout.addWidget(back_button)

        header_frame, header = card()
        self._detail_title = QLabel("")
        self._detail_title.setStyleSheet(
            f"font-size: 18px; font-weight: 600; font-family: {theme.HEADING_FONT_FAMILY};"
        )
        header.addWidget(self._detail_title)

        self._detail_status = QLabel("")
        self._detail_status.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        header.addWidget(self._detail_status)
        layout.addWidget(header_frame)

        contact_frame, contact = card()
        contact.addWidget(section_label("Datos de contacto"))
        grid = ResponsiveGrid(min_cell_width=220)
        email_field, self._detail_email = labeled_field("Email")
        grid.add_widget(email_field)
        phone_field, self._detail_phone = labeled_field("Teléfono")
        grid.add_widget(phone_field)
        contact.addWidget(grid)
        address_field, self._detail_address = labeled_field("Dirección")
        contact.addWidget(address_field)
        income_field, self._detail_income = labeled_field(
            "Ingreso mensual declarado (Gs)", input_cls=CurrencyInput
        )
        contact.addWidget(income_field)
        source_of_funds_field, self._detail_source_of_funds = labeled_field(
            "Origen de fondos"
        )
        contact.addWidget(source_of_funds_field)
        layout.addWidget(contact_frame)

        (
            extended_frame,
            self._detail_national_id_expiry_date,
            self._detail_marital_status,
            self._detail_education_level,
            self._detail_occupation,
            self._detail_neighborhood,
            self._detail_risk_rating,
            self._detail_economic_sector,
        ) = _extended_profile_card()
        layout.addWidget(extended_frame)

        (
            (
                ref1_frame,
                self._detail_ref1_name,
                self._detail_ref1_relationship,
                self._detail_ref1_phone,
            ),
            (
                ref2_frame,
                self._detail_ref2_name,
                self._detail_ref2_relationship,
                self._detail_ref2_phone,
            ),
            (
                job_frame,
                self._detail_ref_job_employer,
                self._detail_ref_job_position,
                self._detail_ref_job_phone,
                self._detail_ref_job_seniority,
            ),
        ) = _reference_cards()
        layout.addWidget(ref1_frame)
        layout.addWidget(ref2_frame)
        layout.addWidget(job_frame)

        actions_frame, actions_card = card()
        actions_card.addWidget(section_label("Acciones"))
        actions = QHBoxLayout()

        self._save_button = QPushButton("Guardar cambios")
        self._save_button.setStyleSheet(theme.accent_button_style())
        self._save_button.clicked.connect(self._on_update_submit)
        actions.addWidget(self._save_button)

        self._deactivate_button = QPushButton("Desactivar cliente")
        self._deactivate_button.setStyleSheet(theme.secondary_button_style())
        self._deactivate_button.clicked.connect(self._on_deactivate)
        actions.addWidget(self._deactivate_button)

        loans_button = QPushButton("Ver préstamos")
        loans_button.setStyleSheet(theme.secondary_button_style())
        loans_button.clicked.connect(self._on_view_loans)
        actions.addWidget(loans_button)
        actions_card.addLayout(actions)

        national_id_row = QHBoxLayout()
        national_id_field, self._new_national_id_input = labeled_field(
            "Nuevo documento de identidad"
        )
        national_id_row.addWidget(national_id_field, stretch=1)
        button_column = QVBoxLayout()
        button_column.setContentsMargins(0, 0, 0, 0)
        button_column.setSpacing(4)
        button_column.addWidget(QLabel(""))
        self._change_national_id_button = QPushButton("Cambiar documento (BR-CLI-003)")
        self._change_national_id_button.setStyleSheet(theme.secondary_button_style())
        self._change_national_id_button.clicked.connect(self._on_change_national_id)
        button_column.addWidget(self._change_national_id_button)
        national_id_row.addLayout(button_column)
        actions_card.addLayout(national_id_row)
        layout.addWidget(actions_frame)

        layout.addStretch()
        return page

    def apply_role(self, role: str) -> None:
        """BR-CAJA-005: el Cajero consulta la ficha del cliente para poder
        informarle, pero no la crea ni la edita -- CreateClient/UpdateClient
        son CREDIT_ANALYST_AND_ABOVE en rbac.py. Ocultar los botones evita
        ofrecerle una acción que solo volvería como PERMISSION_DENIED; el
        servidor sigue siendo la autoridad, esto es UX."""
        puede_originar = can_originate_credit(role)
        self._new_button.setVisible(puede_originar)
        self._save_button.setVisible(puede_originar)

    def open_client_detail(self, client_id: str) -> None:
        """Entry point used by MainWindow when navigating here from LoansView's
        client search ("Ver ficha del cliente")."""
        self._load_detail(client_id)

    def _load_detail(self, client_id: str) -> None:
        self._set_loading(True)
        self._worker = AsyncWorker(
            self._client.get_client_by_id,
            self._session.access_token,
            client_id,
            error_translator=_friendly_message,
        )
        self._worker.succeeded.connect(self._on_detail_loaded)
        self._worker.failed.connect(self._on_error)
        self._worker.finished.connect(lambda: self._set_loading(False))
        self._worker.start()

    def _on_detail_loaded(self, client) -> None:
        self._selected_client_id = client.id
        self._detail_title.setText(f"{client.first_name} {client.last_name}")
        self._detail_status.setText(
            f"Documento: {client.national_id} · "
            f"{'Activo' if client.is_active else 'Inactivo'}"
        )
        self._detail_email.setText(client.email)
        self._detail_phone.setText(client.phone_number)
        self._detail_address.setText(client.address)
        self._detail_income.set_amount(client.declared_monthly_income)
        self._detail_source_of_funds.setText(client.source_of_funds)
        self._detail_national_id_expiry_date.setText(
            fecha(client.national_id_expiry_date)
        )
        self._detail_marital_status.setText(client.marital_status)
        self._detail_education_level.setText(client.education_level)
        self._detail_occupation.setText(client.occupation)
        self._detail_neighborhood.setText(client.neighborhood)
        self._detail_risk_rating.setText(client.risk_rating)
        self._detail_economic_sector.setText(client.economic_sector)
        self._detail_ref1_name.setText(client.personal_reference_1_name)
        self._detail_ref1_relationship.setText(client.personal_reference_1_relationship)
        self._detail_ref1_phone.setText(client.personal_reference_1_phone)
        self._detail_ref2_name.setText(client.personal_reference_2_name)
        self._detail_ref2_relationship.setText(client.personal_reference_2_relationship)
        self._detail_ref2_phone.setText(client.personal_reference_2_phone)
        self._detail_ref_job_employer.setText(client.employment_reference_employer)
        self._detail_ref_job_position.setText(client.employment_reference_position)
        self._detail_ref_job_phone.setText(client.employment_reference_phone)
        self._detail_ref_job_seniority.setText(client.employment_reference_seniority)

        for widget in (
            self._detail_email,
            self._detail_phone,
            self._detail_address,
            self._detail_source_of_funds,
            self._detail_ref1_name,
            self._detail_ref1_relationship,
            self._detail_ref1_phone,
            self._detail_ref2_name,
            self._detail_ref2_relationship,
            self._detail_ref2_phone,
            self._detail_ref_job_employer,
            self._detail_ref_job_position,
            self._detail_ref_job_phone,
            self._detail_ref_job_seniority,
        ):
            widget.set_error(False)

        role = self._session.role
        self._deactivate_button.setEnabled(
            client.is_active and role_at_least(role, "MANAGER")
        )
        can_change_national_id = role_at_least(role, "ADMIN")
        self._new_national_id_input.setEnabled(can_change_national_id)
        self._change_national_id_button.setEnabled(can_change_national_id)

        self._stack.setCurrentIndex(_PAGE_DETAIL)

    def _on_update_submit(self) -> None:
        if self._selected_client_id is None:
            return
        required_fields = (
            (self._detail_email, "email"),
            (self._detail_phone, "phone_number"),
            (self._detail_address, "address"),
            (self._detail_source_of_funds, "source_of_funds"),
            (self._detail_ref1_name, "personal_reference_1_name"),
            (self._detail_ref1_relationship, "personal_reference_1_relationship"),
            (self._detail_ref1_phone, "personal_reference_1_phone"),
            (self._detail_ref2_name, "personal_reference_2_name"),
            (self._detail_ref2_relationship, "personal_reference_2_relationship"),
            (self._detail_ref2_phone, "personal_reference_2_phone"),
            (self._detail_ref_job_employer, "employment_reference_employer"),
            (self._detail_ref_job_position, "employment_reference_position"),
            (self._detail_ref_job_phone, "employment_reference_phone"),
            (self._detail_ref_job_seniority, "employment_reference_seniority"),
        )
        fields = {"client_id": self._selected_client_id}
        has_blank = False
        for widget, field_name in required_fields:
            value = widget.text().strip()
            widget.set_error(not value)
            has_blank = has_blank or not value
            fields[field_name] = value
        if has_blank:
            self._toast.show_message("Complete los campos marcados en rojo.")
            return
        income = self._detail_income.raw_value()
        if income:
            fields["declared_monthly_income"] = income

        fields.update(
            national_id_expiry_date=fecha_a_iso(
                self._detail_national_id_expiry_date.text()
            ),
            marital_status=self._detail_marital_status.text().strip(),
            education_level=self._detail_education_level.text().strip(),
            occupation=self._detail_occupation.text().strip(),
            neighborhood=self._detail_neighborhood.text().strip(),
            risk_rating=self._detail_risk_rating.text().strip(),
            economic_sector=self._detail_economic_sector.text().strip(),
        )

        self._set_loading(True)
        self._worker = AsyncWorker(
            self._client.update_client,
            self._session.access_token,
            error_translator=_friendly_message,
            **fields,
        )
        self._worker.succeeded.connect(
            lambda _r: self._toast.show_message("Cambios guardados.")
        )
        self._worker.failed.connect(self._on_error)
        self._worker.finished.connect(lambda: self._set_loading(False))
        self._worker.start()

    def _on_deactivate(self) -> None:
        if self._selected_client_id is None:
            return
        confirm = QMessageBox.question(
            self,
            "Confirmar",
            "¿Desactivar este cliente? Esta acción requiere que no tenga "
            "préstamos sin pagar (BR-CLI-004).",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        self._set_loading(True)
        self._worker = AsyncWorker(
            self._client.deactivate_client,
            self._session.access_token,
            self._selected_client_id,
            error_translator=_friendly_message,
        )
        self._worker.succeeded.connect(self._on_deactivate_success)
        self._worker.failed.connect(self._on_error)
        self._worker.finished.connect(lambda: self._set_loading(False))
        self._worker.start()

    def _on_deactivate_success(self, _response) -> None:
        self._toast.show_message("Cliente desactivado.")
        self._load_detail(self._selected_client_id)

    def _on_change_national_id(self) -> None:
        if self._selected_client_id is None:
            return
        new_national_id = self._new_national_id_input.text().strip()
        if not new_national_id:
            self._toast.show_message("Ingrese el nuevo número de documento.")
            return

        self._set_loading(True)
        self._worker = AsyncWorker(
            self._client.update_national_id,
            self._session.access_token,
            self._selected_client_id,
            new_national_id,
            error_translator=_friendly_message,
        )
        self._worker.succeeded.connect(self._on_change_national_id_success)
        self._worker.failed.connect(self._on_error)
        self._worker.finished.connect(lambda: self._set_loading(False))
        self._worker.start()

    def _on_change_national_id_success(self, _response) -> None:
        self._new_national_id_input.clear()
        self._toast.show_message("Documento de identidad actualizado.")
        self._load_detail(self._selected_client_id)

    def _on_view_loans(self) -> None:
        if self._selected_client_id is None:
            return
        self.view_loans_requested.emit(
            self._selected_client_id, self._detail_title.text()
        )

    # ---- Helpers comunes -------------------------------------------------

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
