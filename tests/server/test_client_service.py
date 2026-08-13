from datetime import date, datetime, timezone
from decimal import Decimal

import grpc
import pytest

import client_service_pb2

from cas_server.db.base import SessionLocal
from cas_server.db.models import AuditLog, Client, Loan, LoanStatusEnum
from cas_server.services.client_service import ClientServicer

from tests.server.helpers import AbortCalled, FakeContext

# Not just references despite the name -- also carries source_of_funds
# (BR-CLI-006), bundled here since every CreateClient/UpdateClient call in this
# file needs both to be valid.
_VALID_REFERENCES = dict(
    source_of_funds="Salario",
    personal_reference_1_name="Maria Gomez",
    personal_reference_1_relationship="Hermana",
    personal_reference_1_phone="0981111111",
    personal_reference_2_name="Carlos Ruiz",
    personal_reference_2_relationship="Amigo",
    personal_reference_2_phone="0981111112",
    employment_reference_employer="ACME S.A.",
    employment_reference_position="Vendedor",
    employment_reference_phone="0981111113",
    employment_reference_seniority="3 años",
)


def _create_client(
    first_name="Ana",
    last_name="Gomez",
    national_id="1111111",
    email="ana@example.com",
    phone_number="0981000000",
    date_of_birth="1990-01-01",
    address="Calle Falsa 123",
    declared_monthly_income=None,
    source_of_funds="Salario",
    is_active=True,
):
    with SessionLocal() as session:
        client = Client(
            first_name=first_name,
            last_name=last_name,
            national_id=national_id,
            email=email,
            phone_number=phone_number,
            date_of_birth=date.fromisoformat(date_of_birth),
            address=address,
            declared_monthly_income=declared_monthly_income,
            source_of_funds=source_of_funds,
            is_active=is_active,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(client)
        session.commit()
        session.refresh(client)
        return client.id


@pytest.fixture
def servicer():
    return ClientServicer()


def test_create_client_success_returns_id_and_timestamp(servicer):
    response = servicer.CreateClient(
        client_service_pb2.CreateClientRequest(
            first_name="Ana",
            last_name="Gomez",
            national_id="1111111",
            email="ana@example.com",
            phone_number="0981000000",
            date_of_birth="1990-01-01",
            address="Calle Falsa 123",
            **_VALID_REFERENCES,
        ),
        FakeContext(),
    )
    assert response.client_id
    assert response.created_at.seconds > 0


def test_create_client_missing_fields_is_invalid_argument(servicer):
    with pytest.raises(AbortCalled) as exc_info:
        servicer.CreateClient(
            client_service_pb2.CreateClientRequest(first_name="Ana"), FakeContext()
        )
    assert exc_info.value.code == grpc.StatusCode.INVALID_ARGUMENT


def test_create_client_missing_reference_is_invalid_argument(servicer):
    incomplete_references = dict(_VALID_REFERENCES)
    del incomplete_references["employment_reference_seniority"]

    with pytest.raises(AbortCalled) as exc_info:
        servicer.CreateClient(
            client_service_pb2.CreateClientRequest(
                first_name="Ana",
                last_name="Gomez",
                national_id="1111199",
                email="ana2@example.com",
                phone_number="0981000099",
                date_of_birth="1990-01-01",
                address="Calle Falsa 123",
                **incomplete_references,
            ),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.INVALID_ARGUMENT


def test_create_client_missing_source_of_funds_is_invalid_argument(servicer):
    incomplete = dict(_VALID_REFERENCES)
    del incomplete["source_of_funds"]

    with pytest.raises(AbortCalled) as exc_info:
        servicer.CreateClient(
            client_service_pb2.CreateClientRequest(
                first_name="Ana",
                last_name="Gomez",
                national_id="1111198",
                email="ana3@example.com",
                phone_number="0981000098",
                date_of_birth="1990-01-01",
                address="Calle Falsa 123",
                **incomplete,
            ),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.INVALID_ARGUMENT


def test_create_client_succeeds_without_extended_profile_fields(servicer):
    """BR-CLI-007: los 7 campos de perfil extendido son opcionales -- a
    diferencia de las referencias/origen de fondos, omitirlos no bloquea el
    alta."""
    response = servicer.CreateClient(
        client_service_pb2.CreateClientRequest(
            first_name="Ana",
            last_name="Gomez",
            national_id="1111197",
            email="ana4@example.com",
            phone_number="0981000097",
            date_of_birth="1990-01-01",
            address="Calle Falsa 123",
            **_VALID_REFERENCES,
        ),
        FakeContext(),
    )
    assert response.client_id

    fetched = servicer.GetClientById(
        client_service_pb2.GetClientByIdRequest(client_id=response.client_id),
        FakeContext(),
    )
    assert fetched.national_id_expiry_date == ""
    assert fetched.marital_status == ""
    assert fetched.education_level == ""
    assert fetched.occupation == ""
    assert fetched.neighborhood == ""
    assert fetched.risk_rating == ""
    assert fetched.economic_sector == ""


def test_create_and_update_round_trip_extended_profile_fields(servicer):
    response = servicer.CreateClient(
        client_service_pb2.CreateClientRequest(
            first_name="Ana",
            last_name="Gomez",
            national_id="1111196",
            email="ana5@example.com",
            phone_number="0981000096",
            date_of_birth="1990-01-01",
            address="Calle Falsa 123",
            national_id_expiry_date="2030-05-01",
            marital_status="Soltera",
            education_level="Universitario",
            occupation="Contadora",
            neighborhood="Santa Ana",
            risk_rating="Muy bajo",
            economic_sector="Servicios",
            **_VALID_REFERENCES,
        ),
        FakeContext(),
    )

    fetched = servicer.GetClientById(
        client_service_pb2.GetClientByIdRequest(client_id=response.client_id),
        FakeContext(),
    )
    assert fetched.national_id_expiry_date == "2030-05-01"
    assert fetched.marital_status == "Soltera"
    assert fetched.education_level == "Universitario"
    assert fetched.occupation == "Contadora"
    assert fetched.neighborhood == "Santa Ana"
    assert fetched.risk_rating == "Muy bajo"
    assert fetched.economic_sector == "Servicios"

    servicer.UpdateClient(
        client_service_pb2.UpdateClientRequest(
            client_id=response.client_id,
            email="ana5@example.com",
            phone_number="0981000096",
            address="Calle Falsa 123",
            marital_status="Casada",
            neighborhood="Villa Morra",
            **_VALID_REFERENCES,
        ),
        FakeContext(),
    )
    updated = servicer.GetClientById(
        client_service_pb2.GetClientByIdRequest(client_id=response.client_id),
        FakeContext(),
    )
    assert updated.marital_status == "Casada"
    assert updated.neighborhood == "Villa Morra"
    # UpdateClient es full-replace: los campos no reenviados quedan vacíos.
    assert updated.education_level == ""


def test_create_client_invalid_national_id_expiry_date_is_invalid_argument(servicer):
    with pytest.raises(AbortCalled) as exc_info:
        servicer.CreateClient(
            client_service_pb2.CreateClientRequest(
                first_name="Ana",
                last_name="Gomez",
                national_id="1111195",
                email="ana6@example.com",
                phone_number="0981000095",
                date_of_birth="1990-01-01",
                address="Calle Falsa 123",
                national_id_expiry_date="not-a-date",
                **_VALID_REFERENCES,
            ),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.INVALID_ARGUMENT


def test_create_client_under_18_is_failed_precondition(servicer):
    with pytest.raises(AbortCalled) as exc_info:
        servicer.CreateClient(
            client_service_pb2.CreateClientRequest(
                first_name="Kid",
                last_name="Doe",
                national_id="2222222",
                email="kid@example.com",
                phone_number="0981000001",
                date_of_birth="2015-01-01",
                address="Calle Falsa 456",
                **_VALID_REFERENCES,
            ),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.FAILED_PRECONDITION


def test_create_client_duplicate_national_id_is_already_exists(servicer):
    _create_client(national_id="3333333", email="first@example.com")

    with pytest.raises(AbortCalled) as exc_info:
        servicer.CreateClient(
            client_service_pb2.CreateClientRequest(
                first_name="Other",
                last_name="Person",
                national_id="3333333",
                email="second@example.com",
                phone_number="0981000002",
                date_of_birth="1985-05-05",
                address="Otra Calle 1",
                **_VALID_REFERENCES,
            ),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.ALREADY_EXISTS


def test_create_client_duplicate_email_is_already_exists(servicer):
    _create_client(national_id="4444444", email="dup@example.com")

    with pytest.raises(AbortCalled) as exc_info:
        servicer.CreateClient(
            client_service_pb2.CreateClientRequest(
                first_name="Other",
                last_name="Person",
                national_id="5555555",
                email="dup@example.com",
                phone_number="0981000003",
                date_of_birth="1985-05-05",
                address="Otra Calle 2",
                **_VALID_REFERENCES,
            ),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.ALREADY_EXISTS


def test_get_client_by_id_not_found(servicer):
    with pytest.raises(AbortCalled) as exc_info:
        servicer.GetClientById(
            client_service_pb2.GetClientByIdRequest(
                client_id="00000000-0000-0000-0000-000000000000"
            ),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.NOT_FOUND


def test_get_client_by_id_returns_full_profile(servicer):
    client_id = _create_client(
        national_id="6666666",
        email="full@example.com",
        address="Direccion Completa 1",
        declared_monthly_income=2500,
    )

    response = servicer.GetClientById(
        client_service_pb2.GetClientByIdRequest(client_id=str(client_id)), FakeContext()
    )
    assert response.date_of_birth == "1990-01-01"
    assert response.address == "Direccion Completa 1"
    assert response.updated_at.seconds > 0
    assert response.declared_monthly_income == "2500.00"
    assert response.source_of_funds == "Salario"


def test_search_clients_matches_by_name_document_and_phone(servicer):
    _create_client(
        first_name="Carlos",
        last_name="Ruiz",
        national_id="7777777",
        email="carlos@example.com",
        phone_number="0981234567",
    )

    by_name = servicer.SearchClients(
        client_service_pb2.SearchClientsRequest(search_term="Carlos"), FakeContext()
    )
    by_doc = servicer.SearchClients(
        client_service_pb2.SearchClientsRequest(search_term="7777777"), FakeContext()
    )
    by_phone = servicer.SearchClients(
        client_service_pb2.SearchClientsRequest(search_term="1234567"), FakeContext()
    )
    for result in (by_name, by_doc, by_phone):
        assert len(result.clients) == 1
        assert result.clients[0].national_id == "7777777"


def test_search_clients_pagination_next_page_token(servicer):
    for i in range(3):
        _create_client(
            first_name="Client",
            last_name=f"Z{i}",
            national_id=f"800000{i}",
            email=f"page{i}@example.com",
            phone_number=f"098100000{i}",
        )

    first_page = servicer.SearchClients(
        client_service_pb2.SearchClientsRequest(
            search_term="", page_size=2, page_token=0
        ),
        FakeContext(),
    )
    assert len(first_page.clients) == 2
    assert first_page.next_page_token == 2

    second_page = servicer.SearchClients(
        client_service_pb2.SearchClientsRequest(
            search_term="", page_size=2, page_token=2
        ),
        FakeContext(),
    )
    assert len(second_page.clients) == 1
    assert second_page.next_page_token == 0


def test_update_client_success_updates_contact_fields(servicer):
    client_id = _create_client(national_id="9999999", email="before@example.com")

    response = servicer.UpdateClient(
        client_service_pb2.UpdateClientRequest(
            client_id=str(client_id),
            email="after@example.com",
            phone_number="0987654321",
            address="Nueva Direccion",
            declared_monthly_income="3000.00",
            **_VALID_REFERENCES,
        ),
        FakeContext(),
    )
    assert response.success

    fetched = servicer.GetClientById(
        client_service_pb2.GetClientByIdRequest(client_id=str(client_id)), FakeContext()
    )
    assert fetched.email == "after@example.com"
    assert fetched.address == "Nueva Direccion"
    assert fetched.declared_monthly_income == "3000.00"


def test_update_client_email_conflict_is_already_exists(servicer):
    _create_client(national_id="1010101", email="taken@example.com")
    client_id = _create_client(national_id="1010102", email="mine@example.com")

    with pytest.raises(AbortCalled) as exc_info:
        servicer.UpdateClient(
            client_service_pb2.UpdateClientRequest(
                client_id=str(client_id),
                email="taken@example.com",
                phone_number="0981000009",
                address="Direccion X",
                **_VALID_REFERENCES,
            ),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.ALREADY_EXISTS


def test_deactivate_client_blocked_by_non_paid_loan(servicer):
    client_id = _create_client(national_id="1212121", email="hasloan@example.com")
    with SessionLocal() as session:
        session.add(
            Loan(
                client_id=client_id,
                principal_amount=Decimal("1000.00"),
                interest_rate=Decimal("0.10"),
                term_months=6,
                first_due_date=datetime.now(timezone.utc).date(),
                status=LoanStatusEnum.PENDING,
                created_at=datetime.now(timezone.utc),
            )
        )
        session.commit()

    with pytest.raises(AbortCalled) as exc_info:
        servicer.DeactivateClient(
            client_service_pb2.DeactivateClientRequest(client_id=str(client_id)),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.FAILED_PRECONDITION


def test_deactivate_client_success_when_only_paid_loans(servicer):
    client_id = _create_client(national_id="1313131", email="paidloan@example.com")
    with SessionLocal() as session:
        session.add(
            Loan(
                client_id=client_id,
                principal_amount=Decimal("1000.00"),
                interest_rate=Decimal("0.10"),
                term_months=6,
                first_due_date=datetime.now(timezone.utc).date(),
                status=LoanStatusEnum.PAID,
                created_at=datetime.now(timezone.utc),
            )
        )
        session.commit()

    response = servicer.DeactivateClient(
        client_service_pb2.DeactivateClientRequest(client_id=str(client_id)),
        FakeContext(),
    )
    assert response.success

    fetched = servicer.GetClientById(
        client_service_pb2.GetClientByIdRequest(client_id=str(client_id)), FakeContext()
    )
    assert fetched.is_active is False


def test_deactivate_client_already_inactive_is_failed_precondition(servicer):
    client_id = _create_client(
        national_id="1414141", email="inactive@example.com", is_active=False
    )

    with pytest.raises(AbortCalled) as exc_info:
        servicer.DeactivateClient(
            client_service_pb2.DeactivateClientRequest(client_id=str(client_id)),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.FAILED_PRECONDITION


def test_update_national_id_success_and_audited(servicer):
    client_id = _create_client(national_id="1515151", email="natid@example.com")

    response = servicer.UpdateNationalId(
        client_service_pb2.UpdateNationalIdRequest(
            client_id=str(client_id), new_national_id="1616161"
        ),
        FakeContext(),
    )
    assert response.success

    with SessionLocal() as session:
        entry = (
            session.query(AuditLog)
            .filter(AuditLog.action.like("CLIENTE_DOCUMENTO_CAMBIADO%"))
            .one()
        )
        assert f"client_id={client_id}" in entry.action
        assert "anterior=1515151" in entry.action
        assert "nuevo=1616161" in entry.action


def test_update_national_id_conflict_is_already_exists(servicer):
    _create_client(national_id="1717171", email="one@example.com")
    client_id = _create_client(national_id="1818181", email="two@example.com")

    with pytest.raises(AbortCalled) as exc_info:
        servicer.UpdateNationalId(
            client_service_pb2.UpdateNationalIdRequest(
                client_id=str(client_id), new_national_id="1717171"
            ),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.ALREADY_EXISTS
