import enum
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cas_server.db.base import Base


class RoleEnum(str, enum.Enum):
    CASHIER = "CASHIER"
    CREDIT_ANALYST = "CREDIT_ANALYST"
    MANAGER = "MANAGER"
    ADMIN = "ADMIN"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    username: Mapped[str] = mapped_column(
        String, unique=True, nullable=False, index=True
    )
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[RoleEnum] = mapped_column(
        SAEnum(RoleEnum, name="role_enum", native_enum=True), nullable=False
    )
    # Datos personales del operador (BR-AUTH-006). Se imprimen en los
    # documentos que llevan la firma de un responsable -- el "Asesor
    # responsable" del Cronograma de Pago y el "Registrado por" del
    # Comprobante de Pago -- para que el cliente sepa con quién trató.
    #
    # Nullable a nivel de base como el resto de los campos agregados después
    # (source_of_funds, first_due_date...): el requisito se hace cumplir en
    # el servicer (CreateUser), no con una restricción de esquema, así que
    # los usuarios que ya existían (p. ej. el ADMIN de seed_admin) no
    # necesitaron backfill y siguen funcionando -- los documentos caen de
    # vuelta al `username` cuando no hay nombre cargado.
    #
    # `national_id` NO lleva UNIQUE, a diferencia de Client.national_id: los
    # operadores son pocos y los da de alta un ADMIN que ve la lista
    # completa, y una restricción única acá obligaría a distinguir cuál de
    # las dos (username o C.I.) falló para poder devolver un mensaje
    # correcto desde confirmar_o_duplicado.
    first_name: Mapped[str | None] = mapped_column(String, nullable=True)
    last_name: Mapped[str | None] = mapped_column(String, nullable=True)
    national_id: Mapped[str | None] = mapped_column(String, nullable=True)
    failed_login_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    is_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Not in specs/authentication/README's literal field list, but required to satisfy
    # BR-AUTH-002 ("bloqueo temporal de 15 minutos") without a manual admin unlock.
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_login: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    audit_logs: Mapped[list["AuditLog"]] = relationship(back_populates="user")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    action: Mapped[str] = mapped_column(String, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    user: Mapped["User | None"] = relationship(back_populates="audit_logs")


class RevokedToken(Base):
    """Blacklist of logged-out access tokens (BR-AUTH-003's Logout RPC).

    Persisted in the DB rather than kept in memory so a server restart
    doesn't silently un-revoke a token that was explicitly logged out.
    """

    __tablename__ = "revoked_tokens"

    jti: Mapped[str] = mapped_column(String, primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class Client(Base):
    __tablename__ = "clients"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    first_name: Mapped[str] = mapped_column(String, nullable=False)
    last_name: Mapped[str] = mapped_column(String, nullable=False)
    national_id: Mapped[str] = mapped_column(
        String, unique=True, nullable=False, index=True
    )
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    phone_number: Mapped[str] = mapped_column(String, nullable=False)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Not in specs/clients/README's literal field list, but required to satisfy
    # BR-LOAN-002 ("cuota mensual no debe exceder el 40% de los ingresos declarados
    # del cliente") -- BR-LOAN-002 ties the income figure to the client, not to a
    # single loan application, so it's stored here rather than on Loan.
    declared_monthly_income: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    # Referencias personales y laboral. Igual que LoanPayment.transfer_reference,
    # la columna es nullable a nivel de esquema -- la obligatoriedad (BR-CLI-005)
    # se aplica en client_service.py, no acá, para no requerir backfill.
    personal_reference_1_name: Mapped[str | None] = mapped_column(String, nullable=True)
    personal_reference_1_relationship: Mapped[str | None] = mapped_column(
        String, nullable=True
    )
    personal_reference_1_phone: Mapped[str | None] = mapped_column(
        String, nullable=True
    )
    personal_reference_2_name: Mapped[str | None] = mapped_column(String, nullable=True)
    personal_reference_2_relationship: Mapped[str | None] = mapped_column(
        String, nullable=True
    )
    personal_reference_2_phone: Mapped[str | None] = mapped_column(
        String, nullable=True
    )
    employment_reference_employer: Mapped[str | None] = mapped_column(
        String, nullable=True
    )
    employment_reference_position: Mapped[str | None] = mapped_column(
        String, nullable=True
    )
    employment_reference_phone: Mapped[str | None] = mapped_column(
        String, nullable=True
    )
    employment_reference_seniority: Mapped[str | None] = mapped_column(
        String, nullable=True
    )
    # Origen de fondos. Igual que las referencias de arriba, la columna es
    # nullable a nivel de esquema -- la obligatoriedad (BR-CLI-006) se aplica
    # en client_service.py, no acá.
    source_of_funds: Mapped[str | None] = mapped_column(String, nullable=True)
    # Perfil extendido -- BR-CLI-007. Todos opcionales (a diferencia de las
    # referencias/origen de fondos de arriba): enriquecen el perfil sin bloquear
    # el alta ni UpdateClient.
    national_id_expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    marital_status: Mapped[str | None] = mapped_column(String, nullable=True)
    education_level: Mapped[str | None] = mapped_column(String, nullable=True)
    occupation: Mapped[str | None] = mapped_column(String, nullable=True)
    neighborhood: Mapped[str | None] = mapped_column(String, nullable=True)
    risk_rating: Mapped[str | None] = mapped_column(String, nullable=True)
    economic_sector: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    loans: Mapped[list["Loan"]] = relationship(back_populates="client")


class LoanStatusEnum(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    PAID = "PAID"
    DEFAULTED = "DEFAULTED"
    # Not in specs/loans/README's literal enum list, but required to satisfy
    # BR-LOAN-003 (an "Aprobado" loan not disbursed within 30 days becomes
    # "Caducado"): DEFAULTED is semantically wrong for a loan that was never
    # disbursed, so this adds a dedicated terminal state instead.
    EXPIRED = "EXPIRED"


class Loan(Base):
    __tablename__ = "loans"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clients.id"), nullable=False, index=True
    )
    # Asesor que registró la solicitud -- puramente informativo (se muestra en
    # el cronograma de pago entregado al cliente), no una regla de negocio.
    # Nullable porque los préstamos creados antes de este campo (o vía un
    # test que llama al servicer directo sin AuthInterceptor, ver
    # BR-LOAN-007's mismo patrón de `claims is None`) no tienen un actor
    # conocido -- no se hace backfill.
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    principal_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    interest_rate: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    term_months: Mapped[int] = mapped_column(Integer, nullable=False)
    # Fecha del primer vencimiento -- BR-LOAN-004. Se calcula automáticamente en
    # CreateLoan (created_at + LOAN_DEFAULT_FIRST_DUE_DAYS) y es editable luego,
    # junto con principal_amount/term_months, vía UpdateLoanProposal mientras el
    # préstamo esté PENDING.
    first_due_date: Mapped[date] = mapped_column(Date, nullable=False)
    # Garantía de respaldo -- BR-LOAN-005. Opcional; si se completa, ambos campos
    # se completan juntos (aplicado/enforced en loan_service.py, no acá).
    guarantee_type: Mapped[str | None] = mapped_column(String, nullable=True)
    guarantee_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    # Cargos y seguros -- BR-LOAN-006. Puramente informativos: no afectan la
    # cuota, BR-LOAN-002, ni la lógica de cobro/pago (RecordPayment,
    # _totales_prestamo).
    charge_interest_tax: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    charge_admin_fee: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    charge_cancellation_insurance: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    charge_contracted_insurance: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    status: Mapped[LoanStatusEnum] = mapped_column(
        SAEnum(LoanStatusEnum, name="loan_status_enum", native_enum=True),
        nullable=False,
        default=LoanStatusEnum.PENDING,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    # Not in specs/loans/README's literal field list, but required to compute
    # BR-LOAN-003's 30-day disbursement window -- there's no other timestamp in
    # the literal spec marking when that clock starts.
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    client: Mapped["Client"] = relationship(back_populates="loans")
    payments: Mapped[list["LoanPayment"]] = relationship(back_populates="loan")
    installment_adjustments: Mapped[list["LoanInstallmentAdjustment"]] = relationship(
        back_populates="loan"
    )


class LoanPayment(Base):
    """Minimal payment ledger backing RecordPayment / the ACTIVE->PAID transition.

    Not in specs/loans/README's literal data model (no schedule/ledger table is
    listed), but some persisted state is required to know when a loan's computed
    amortization schedule (cas_server/services/amortization.py) has been paid
    off. A ledger keeps individual payment history rather than collapsing it
    into a counter, consistent with this system's AuditLog-driven traceability.
    """

    __tablename__ = "loan_payments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    loan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("loans.id"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    # Código/número de transferencia, descuento directo o descuento en cuenta
    # específica -- el cobro en efectivo ya no se usa (rol CASHIER fuera de
    # uso), así que loan_service.py exige este dato en la capa de aplicación.
    # Nullable a nivel de columna únicamente para no requerir backfill de
    # filas históricas si algún día existieran.
    transfer_reference: Mapped[str | None] = mapped_column(String, nullable=True)
    paid_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    loan: Mapped["Loan"] = relationship(back_populates="payments")


class LoanInstallmentAdjustment(Base):
    """Excepción manual al cronograma calculado (amortization.py) para una
    cuota puntual -- el cronograma en sí no se persiste, solo estas
    excepciones, que se aplican como override al recalcularlo (ver
    LoanServicer.UpdateInstallmentAmount / calcular_cronograma's `ajustes`).
    Mismo patrón que las demás columnas nullable/tablas de excepción de este
    modelo: la validación de negocio vive en loan_service.py, no acá.
    """

    __tablename__ = "loan_installment_adjustments"
    __table_args__ = (
        UniqueConstraint(
            "loan_id", "installment_number", name="uq_loan_installment_adjustment"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    loan_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("loans.id"), nullable=False, index=True
    )
    installment_number: Mapped[int] = mapped_column(Integer, nullable=False)
    adjusted_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    loan: Mapped["Loan"] = relationship(back_populates="installment_adjustments")
