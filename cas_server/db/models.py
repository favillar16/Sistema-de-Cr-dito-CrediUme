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
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
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


class PaymentMethodEnum(str, enum.Enum):
    """Medio por el que entró un pago de cuota (BR-CAJA-004).

    EFECTIVO se cobra en ventanilla y queda imputado a la caja abierta del
    cajero que lo registró (ver CashMovement); TRANSFERENCIA cubre todo lo
    que no pasa por la caja -- transferencia bancaria, débito automático o
    descuento en cuenta -- y sigue exigiendo `transfer_reference`.
    """

    EFECTIVO = "EFECTIVO"
    TRANSFERENCIA = "TRANSFERENCIA"


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
    # BR-CAJA-004. Nullable porque la columna se agregó después: los pagos
    # anteriores a la reincorporación de la caja son todos por transferencia
    # (era el único medio admitido), así que un NULL se lee como
    # TRANSFERENCIA en vez de requerir backfill -- mismo criterio que
    # transfer_reference arriba.
    payment_method: Mapped[PaymentMethodEnum | None] = mapped_column(
        SAEnum(PaymentMethodEnum, name="payment_method_enum", native_enum=True),
        nullable=True,
    )
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


class CashSessionStatusEnum(str, enum.Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class CashSession(Base):
    """Turno de caja de un cajero: se abre declarando el efectivo inicial y se
    cierra declarando el efectivo contado (arqueo) -- BR-CAJA-001/BR-CAJA-003.

    El "esperado" del cierre NO se guarda como un dato que mande el cliente:
    se recalcula en el servidor a partir de `opening_amount` más los
    CashMovement del turno, y recién ahí se compara contra lo contado. Se
    persisten los tres números (esperado, contado, diferencia) porque el
    arqueo es justamente el registro histórico de esa comparación -- si solo
    guardáramos los movimientos, un ajuste posterior cambiaría el resultado
    de un arqueo ya firmado.
    """

    __tablename__ = "cash_sessions"
    __table_args__ = (
        # BR-CAJA-002: un cajero no puede tener dos turnos abiertos a la vez.
        # Índice único parcial (solo sobre las filas OPEN) en vez de una
        # verificación previa en el servicer: dos aperturas concurrentes del
        # mismo usuario harían cada una su propio SELECT y ninguna vería a la
        # otra, exactamente la clase de carrera que ES-006 §3.1 documenta.
        # Con esto la base rechaza a la perdedora y confirmar_o_duplicado la
        # traduce a ALREADY_EXISTS.
        Index(
            "uq_cash_session_open_por_usuario",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'OPEN'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    status: Mapped[CashSessionStatusEnum] = mapped_column(
        SAEnum(
            CashSessionStatusEnum, name="cash_session_status_enum", native_enum=True
        ),
        nullable=False,
        default=CashSessionStatusEnum.OPEN,
    )
    opening_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    opening_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Los tres campos del arqueo -- nulos mientras el turno siga abierto.
    closing_expected_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), nullable=True
    )
    closing_counted_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), nullable=True
    )
    closing_difference: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closing_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Quién cerró el turno -- normalmente el mismo cajero, pero un Gerente
    # puede cerrar una caja que quedó abierta (BR-CAJA-003), y en ese caso el
    # arqueo tiene que decir quién lo firmó.
    closed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )

    user: Mapped["User"] = relationship(foreign_keys=[user_id])
    closed_by: Mapped["User | None"] = relationship(foreign_keys=[closed_by_user_id])
    movements: Mapped[list["CashMovement"]] = relationship(
        back_populates="cash_session"
    )


class CashMovementTypeEnum(str, enum.Enum):
    INGRESO = "INGRESO"
    EGRESO = "EGRESO"


class CashMovement(Base):
    """Entrada o salida de efectivo dentro de un turno de caja (BR-CAJA-002).

    Dos orígenes: los que carga el cajero a mano (con su concepto) y los
    automáticos que genera RecordPayment cuando cobra una cuota en efectivo
    (BR-CAJA-004) -- esos llevan `loan_payment_id` para poder rastrear el
    movimiento hasta el pago que lo produjo, y no son editables desde la
    pantalla de caja.
    """

    __tablename__ = "cash_movements"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    cash_session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cash_sessions.id"), nullable=False, index=True
    )
    movement_type: Mapped[CashMovementTypeEnum] = mapped_column(
        SAEnum(CashMovementTypeEnum, name="cash_movement_type_enum", native_enum=True),
        nullable=False,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    concept: Mapped[str] = mapped_column(String, nullable=False)
    # Solo en los movimientos generados por un cobro de cuota en efectivo.
    loan_payment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("loan_payments.id"), nullable=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    cash_session: Mapped["CashSession"] = relationship(back_populates="movements")
