"""Bulk-seed a demo portfolio: 10 clients, 10 loans spread across different
historical dates and every lifecycle status, to show the system's data
volume and behavior end-to-end (dashboard aggregates, loan list, schedules).

Usage (from repo root, venv active):
    python -m cas_server.scripts.seed_demo_loans

Loans are created through the real LoanServicer (CreateLoan/ApproveLoan/
DisburseLoan/RecordPayment/MarkDefaulted, direct-call style like
tests/server/test_loan_service.py) so amortization/validation stay
consistent with production logic. Dates are backdated by editing the rows
*after* each lifecycle-advancing call has already succeeded under the real
current timestamp -- never before, since e.g. DisburseLoan itself re-checks
BR-LOAN-003's 30-day lazy-expiry against Loan.approved_at, and a
prematurely-backdated approved_at would make the call reject its own loan.
"""

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_UP, Decimal

import client_service_pb2
import loan_service_pb2

from cas_server.db.base import SessionLocal
from cas_server.db.models import Client, Loan, LoanPayment
from cas_server.services.amortization import calcular_cronograma
from cas_server.services.client_service import ClientServicer
from cas_server.services.loan_service import LoanServicer, _totales_prestamo


class _ScriptContext:
    """Minimal grpc.ServicerContext stand-in for direct-call seeding, same
    shape as tests/server/helpers.py's FakeContext (not imported from there
    since tests/ isn't a runtime dependency of cas_server/scripts/)."""

    def abort(self, code, message):
        raise RuntimeError(f"{code}: {message}")

    def peer(self):
        return "ipv4:127.0.0.1:1234"


_CTX = _ScriptContext()


def _income_for(principal: Decimal, rate: Decimal, term: int) -> Decimal:
    """Sets declared_monthly_income so the installment lands at ~30% of
    income -- comfortably under BR-LOAN-002's 40% cap regardless of rounding."""
    cuota = calcular_cronograma(principal, rate, term)[0].monto_cuota
    income = (cuota / Decimal("0.30")).quantize(Decimal("100000"), rounding=ROUND_UP)
    return income


def _crear_cliente(servicer: ClientServicer, bio: dict, income: Decimal) -> str:
    response = servicer.CreateClient(
        client_service_pb2.CreateClientRequest(
            first_name=bio["first_name"],
            last_name=bio["last_name"],
            national_id=bio["national_id"],
            date_of_birth=bio["date_of_birth"],
            email=bio["email"],
            phone_number=bio["phone_number"],
            address=bio["address"],
            declared_monthly_income=str(income),
            source_of_funds=bio["source_of_funds"],
            personal_reference_1_name=bio["ref1_name"],
            personal_reference_1_relationship=bio["ref1_rel"],
            personal_reference_1_phone=bio["ref1_phone"],
            personal_reference_2_name=bio["ref2_name"],
            personal_reference_2_relationship=bio["ref2_rel"],
            personal_reference_2_phone=bio["ref2_phone"],
            employment_reference_employer=bio["employer"],
            employment_reference_position=bio["position"],
            employment_reference_phone=bio["employer_phone"],
            employment_reference_seniority=bio["seniority"],
        ),
        _CTX,
    )
    return response.client_id


def _backdate_client(client_id: str, created_at: datetime) -> None:
    with SessionLocal() as sesion:
        cliente = sesion.get(Client, uuid.UUID(client_id))
        cliente.created_at = created_at
        cliente.updated_at = created_at
        sesion.commit()


def _crear_prestamo(
    servicer: LoanServicer, client_id: str, principal: Decimal, rate: Decimal, term: int
) -> str:
    response = servicer.CreateLoan(
        loan_service_pb2.CreateLoanRequest(
            client_id=client_id,
            principal_amount=str(principal),
            interest_rate=str(rate),
            term_months=term,
        ),
        _CTX,
    )
    return response.loan_id


def _backdate_loan(
    loan_id: str, created_at: datetime, approved_at: datetime | None
) -> None:
    with SessionLocal() as sesion:
        prestamo = sesion.get(Loan, uuid.UUID(loan_id))
        prestamo.created_at = created_at
        prestamo.first_due_date = created_at.date() + timedelta(days=30)
        if approved_at is not None:
            prestamo.approved_at = approved_at
        sesion.commit()


def _backdate_payments(loan_id: str, payment_dates: list[datetime]) -> None:
    with SessionLocal() as sesion:
        pagos = (
            sesion.query(LoanPayment)
            .filter(LoanPayment.loan_id == uuid.UUID(loan_id))
            .order_by(LoanPayment.paid_at)
            .all()
        )
        for pago, fecha in zip(pagos, payment_dates):
            pago.paid_at = fecha
        sesion.commit()


def _dt(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 10, 0, tzinfo=timezone.utc)


# Each entry: client bio, loan terms, target lifecycle, and the historical
# dates that lifecycle should end up stamped with.
_HISTORIAS = [
    dict(
        bio=dict(
            first_name="Rosa",
            last_name="Benitez",
            national_id="3345678",
            date_of_birth="1985-03-14",
            email="rosa.benitez@example.com.py",
            phone_number="0981123456",
            address="Av. Mariscal Lopez 1234, Asuncion",
            source_of_funds="Salario",
            ref1_name="Elva Benitez",
            ref1_rel="Hermana",
            ref1_phone="0981000001",
            ref2_name="Marcos Ayala",
            ref2_rel="Vecino",
            ref2_phone="0981000002",
            employer="Supermercados Real",
            position="Cajera",
            employer_phone="0981000003",
            seniority="4 años",
        ),
        principal=Decimal("6000000"),
        rate=Decimal("0.24"),
        term=12,
        estado="PAID",
        creado=date(2025, 11, 5),
        aprobado=date(2025, 11, 12),
        pagos=[
            (Decimal("0.55"), date(2025, 12, 20)),
            (Decimal("1.00"), date(2026, 4, 15)),
        ],
    ),
    dict(
        bio=dict(
            first_name="Julio Cesar",
            last_name="Aquino",
            national_id="3456781",
            date_of_birth="1978-07-22",
            email="julio.aquino@example.com.py",
            phone_number="0982123456",
            address="Ruta 2 Km 15, San Lorenzo",
            source_of_funds="Ingresos independientes",
            ref1_name="Nilda Aquino",
            ref1_rel="Esposa",
            ref1_phone="0982000001",
            ref2_name="Ruben Diaz",
            ref2_rel="Socio",
            ref2_phone="0982000002",
            employer="Taller JC (propio)",
            position="Dueño",
            employer_phone="0982000003",
            seniority="9 años",
        ),
        principal=Decimal("3500000"),
        rate=Decimal("0.24"),
        term=6,
        estado="PAID",
        creado=date(2025, 12, 18),
        aprobado=date(2025, 12, 22),
        pagos=[(Decimal("1.00"), date(2026, 3, 1))],
    ),
    dict(
        bio=dict(
            first_name="Marta",
            last_name="Noguera",
            national_id="2987654",
            date_of_birth="1990-01-30",
            email="marta.noguera@example.com.py",
            phone_number="0983123456",
            address="Barrio Obrero, Encarnacion",
            source_of_funds="Salario",
            ref1_name="Dora Noguera",
            ref1_rel="Madre",
            ref1_phone="0983000001",
            ref2_name="Felipe Ortiz",
            ref2_rel="Amigo",
            ref2_phone="0983000002",
            employer="Textiles del Sur",
            position="Supervisora",
            employer_phone="0983000003",
            seniority="2 años",
        ),
        principal=Decimal("9000000"),
        rate=Decimal("0.30"),
        term=18,
        estado="DEFAULTED",
        creado=date(2026, 1, 10),
        aprobado=date(2026, 1, 15),
        pagos=[(Decimal("0.30"), date(2026, 2, 20))],
    ),
    dict(
        bio=dict(
            first_name="Hector",
            last_name="Bareiro",
            national_id="4012345",
            date_of_birth="1982-09-09",
            email="hector.bareiro@example.com.py",
            phone_number="0984123456",
            address="Villa Elisa, Central",
            source_of_funds="Salario",
            ref1_name="Celia Bareiro",
            ref1_rel="Esposa",
            ref1_phone="0984000001",
            ref2_name="Anibal Roa",
            ref2_rel="Compañero de trabajo",
            ref2_phone="0984000002",
            employer="Cooperativa Colonias Unidas",
            position="Administrativo",
            employer_phone="0984000003",
            seniority="6 años",
        ),
        principal=Decimal("4500000"),
        rate=Decimal("0.24"),
        term=12,
        estado="ACTIVE_PARCIAL",
        creado=date(2026, 2, 14),
        aprobado=date(2026, 2, 18),
        pagos=[(Decimal("0.35"), date(2026, 4, 1))],
    ),
    dict(
        bio=dict(
            first_name="Lorena",
            last_name="Caceres",
            national_id="3789012",
            date_of_birth="1988-05-17",
            email="lorena.caceres@example.com.py",
            phone_number="0985123456",
            address="Luque, Central",
            source_of_funds="Salario",
            ref1_name="Ines Caceres",
            ref1_rel="Hermana",
            ref1_phone="0985000001",
            ref2_name="Walter Franco",
            ref2_rel="Vecino",
            ref2_phone="0985000002",
            employer="Itau Paraguay",
            position="Analista",
            employer_phone="0985000003",
            seniority="3 años",
        ),
        principal=Decimal("12000000"),
        rate=Decimal("0.18"),
        term=24,
        estado="ACTIVE_PARCIAL",
        creado=date(2026, 3, 22),
        aprobado=date(2026, 3, 25),
        pagos=[
            (Decimal("0.20"), date(2026, 6, 10)),
            (Decimal("0.15"), date(2026, 7, 25)),
        ],
    ),
    dict(
        bio=dict(
            first_name="Andres",
            last_name="Portillo",
            national_id="4123456",
            date_of_birth="1995-11-02",
            email="andres.portillo@example.com.py",
            phone_number="0986123456",
            address="Fernando de la Mora, Central",
            source_of_funds="Salario",
            ref1_name="Gladys Portillo",
            ref1_rel="Madre",
            ref1_phone="0986000001",
            ref2_name="Sergio Benegas",
            ref2_rel="Amigo",
            ref2_phone="0986000002",
            employer="Transporte Rapido SA",
            position="Chofer",
            employer_phone="0986000003",
            seniority="1 año",
        ),
        principal=Decimal("2500000"),
        rate=Decimal("0.24"),
        term=6,
        estado="ACTIVE_SIN_PAGO",
        creado=date(2026, 4, 30),
        aprobado=date(2026, 5, 4),
        pagos=[],
    ),
    dict(
        bio=dict(
            first_name="Cynthia",
            last_name="Espinola",
            national_id="3654321",
            date_of_birth="1980-02-25",
            email="cynthia.espinola@example.com.py",
            phone_number="0987123456",
            address="Ciudad del Este, Alto Parana",
            source_of_funds="Ingresos independientes",
            ref1_name="Pedro Espinola",
            ref1_rel="Hermano",
            ref1_phone="0987000001",
            ref2_name="Miriam Sanchez",
            ref2_rel="Vecina",
            ref2_phone="0987000002",
            employer="Comercial Cynthia (propio)",
            position="Dueña",
            employer_phone="0987000003",
            seniority="7 años",
        ),
        principal=Decimal("15000000"),
        rate=Decimal("0.24"),
        term=24,
        estado="EXPIRED",
        creado=date(2026, 5, 15),
        aprobado=date(2026, 5, 20),
        pagos=[],
    ),
    dict(
        bio=dict(
            first_name="Braulio",
            last_name="Mendoza",
            national_id="4234567",
            date_of_birth="1992-06-08",
            email="braulio.mendoza@example.com.py",
            phone_number="0988123456",
            address="Capiata, Central",
            source_of_funds="Salario",
            ref1_name="Zunilda Mendoza",
            ref1_rel="Madre",
            ref1_phone="0988000001",
            ref2_name="Osvaldo Cabral",
            ref2_rel="Amigo",
            ref2_phone="0988000002",
            employer="Farmacia San Roque",
            position="Vendedor",
            employer_phone="0988000003",
            seniority="2 años",
        ),
        principal=Decimal("5000000"),
        rate=Decimal("0.24"),
        term=12,
        estado="APPROVED",
        creado=date(2026, 6, 10),
        aprobado=date(2026, 8, 1),
        pagos=[],
    ),
    dict(
        bio=dict(
            first_name="Nilda",
            last_name="Chamorro",
            national_id="3901234",
            date_of_birth="1975-12-19",
            email="nilda.chamorro@example.com.py",
            phone_number="0989123456",
            address="Villarrica, Guaira",
            source_of_funds="Salario",
            ref1_name="Ramona Chamorro",
            ref1_rel="Hermana",
            ref1_phone="0989000001",
            ref2_name="Blas Riveros",
            ref2_rel="Vecino",
            ref2_phone="0989000002",
            employer="Municipalidad de Villarrica",
            position="Funcionaria",
            employer_phone="0989000003",
            seniority="10 años",
        ),
        principal=Decimal("3000000"),
        rate=Decimal("0.24"),
        term=9,
        estado="PENDING",
        creado=date(2026, 7, 20),
        aprobado=None,
        pagos=[],
    ),
    dict(
        bio=dict(
            first_name="Oscar",
            last_name="Villamayor",
            national_id="4345678",
            date_of_birth="1987-04-11",
            email="oscar.villamayor@example.com.py",
            phone_number="0990123456",
            address="San Lorenzo, Central",
            source_of_funds="Salario",
            ref1_name="Carla Villamayor",
            ref1_rel="Esposa",
            ref1_phone="0990000001",
            ref2_name="Dario Melgarejo",
            ref2_rel="Compañero",
            ref2_phone="0990000002",
            employer="Petropar",
            position="Tecnico",
            employer_phone="0990000003",
            seniority="5 años",
        ),
        principal=Decimal("7000000"),
        rate=Decimal("0.24"),
        term=15,
        estado="PENDING",
        creado=date(2026, 8, 8),
        aprobado=None,
        pagos=[],
    ),
]


def seed_demo_loans() -> None:
    client_servicer = ClientServicer()
    loan_servicer = LoanServicer()
    resumen = []

    for historia in _HISTORIAS:
        income = _income_for(historia["principal"], historia["rate"], historia["term"])
        client_id = _crear_cliente(client_servicer, historia["bio"], income)
        _backdate_client(client_id, _dt(historia["creado"]))

        loan_id = _crear_prestamo(
            loan_servicer,
            client_id,
            historia["principal"],
            historia["rate"],
            historia["term"],
        )

        estado = historia["estado"]
        if estado != "PENDING":
            loan_servicer.ApproveLoan(
                loan_service_pb2.ApproveLoanRequest(loan_id=loan_id), _CTX
            )
        if estado in ("PAID", "DEFAULTED", "ACTIVE_PARCIAL", "ACTIVE_SIN_PAGO"):
            loan_servicer.DisburseLoan(
                loan_service_pb2.DisburseLoanRequest(loan_id=loan_id), _CTX
            )

            if historia["pagos"]:
                with SessionLocal() as sesion:
                    total_programado, _ = _totales_prestamo(
                        sesion.get(Loan, uuid.UUID(loan_id))
                    )
                restante = total_programado
                for i, (fraccion, _fecha) in enumerate(historia["pagos"]):
                    es_ultimo = i == len(historia["pagos"]) - 1
                    if es_ultimo and estado == "PAID":
                        monto = restante
                    else:
                        monto = (total_programado * fraccion).quantize(Decimal("1"))
                    restante -= monto
                    loan_servicer.RecordPayment(
                        loan_service_pb2.RecordPaymentRequest(
                            loan_id=loan_id,
                            amount=str(monto),
                            transfer_reference=f"TRF-{historia['creado'].year}-{loan_id[:6]}",
                        ),
                        _CTX,
                    )

            if estado == "DEFAULTED":
                loan_servicer.MarkDefaulted(
                    loan_service_pb2.MarkDefaultedRequest(loan_id=loan_id), _CTX
                )

        approved_at = _dt(historia["aprobado"]) if historia["aprobado"] else None
        _backdate_loan(loan_id, _dt(historia["creado"]), approved_at)
        if historia["pagos"]:
            _backdate_payments(loan_id, [_dt(f) for _, f in historia["pagos"]])

        resumen.append(
            (
                historia["bio"]["first_name"],
                historia["bio"]["last_name"],
                loan_id,
                estado,
            )
        )

    # Settle any lazy-expiry transitions (BR-LOAN-003) now that approved_at
    # has been backdated, same trigger the app itself uses on every read.
    with SessionLocal() as sesion:
        client_ids = {
            str(sesion.get(Loan, uuid.UUID(loan_id)).client_id)
            for _, _, loan_id, _ in resumen
        }
    for client_id in client_ids:
        loan_servicer.ListClientLoans(
            loan_service_pb2.ListClientLoansRequest(client_id=client_id), _CTX
        )

    print(f"Creados {len(resumen)} prestamos de prueba:\n")
    print(f"{'Cliente':<28} {'Loan ID':<38} Estado final")
    print("-" * 80)
    with SessionLocal() as sesion:
        for first, last, loan_id, _ in resumen:
            prestamo = sesion.get(Loan, uuid.UUID(loan_id))
            print(f"{first + ' ' + last:<28} {loan_id:<38} {prestamo.status.value}")


if __name__ == "__main__":
    seed_demo_loans()
