"""RBAC coverage for ClientService, mirroring test_interceptor_integration.py's
pattern: a real grpc.Server with AuthInterceptor wired in, driven over an
actual channel so metadata-based auth is exercised for real."""

from concurrent import futures
from datetime import date, datetime, timezone

import auth_service_pb2
import auth_service_pb2_grpc
import client_service_pb2
import client_service_pb2_grpc
import grpc
import pytest

from cas_server.db.base import SessionLocal
from cas_server.db.models import Client, RoleEnum, User
from cas_server.security.interceptor import AuthInterceptor
from cas_server.security.passwords import hash_password
from cas_server.services.auth_service import AuthServicer
from cas_server.services.client_service import ClientServicer


@pytest.fixture
def stubs():
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=4), interceptors=[AuthInterceptor()]
    )
    auth_service_pb2_grpc.add_AuthServiceServicer_to_server(AuthServicer(), server)
    client_service_pb2_grpc.add_ClientServiceServicer_to_server(
        ClientServicer(), server
    )
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()

    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    auth_stub = auth_service_pb2_grpc.AuthServiceStub(channel)
    client_stub = client_service_pb2_grpc.ClientServiceStub(channel)
    try:
        yield auth_stub, client_stub
    finally:
        channel.close()
        server.stop(grace=None)


def _create_user(username, password, role):
    with SessionLocal() as session:
        session.add(
            User(username=username, password_hash=hash_password(password), role=role)
        )
        session.commit()


def _create_client_row(national_id="5000001", email="rbac@example.com"):
    with SessionLocal() as session:
        client = Client(
            first_name="RBAC",
            last_name="Target",
            national_id=national_id,
            email=email,
            phone_number="0981222222",
            date_of_birth=date(1990, 1, 1),
            address="Calle RBAC 1",
            is_active=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(client)
        session.commit()
        session.refresh(client)
        return client.id


def _login(auth_stub, username, password):
    response = auth_stub.Login(
        auth_service_pb2.LoginRequest(username=username, password=password)
    )
    return (("authorization", f"Bearer {response.access_token}"),)


def _solicitud_alta_cliente() -> client_service_pb2.CreateClientRequest:
    return client_service_pb2.CreateClientRequest(
        first_name="New",
        last_name="Client",
        national_id="6000001",
        email="new@example.com",
        phone_number="0981333333",
        date_of_birth="1990-01-01",
        address="Calle Nueva 1",
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


def test_create_client_requires_credit_analyst_or_above(stubs):
    """BR-CAJA-005: el cajero es un rol de ventanilla -- consulta y cobra,
    pero el alta de clientes subió a Analista de Crédito cuando el rol volvió
    a usarse."""
    auth_stub, client_stub = stubs
    _create_user("cashier_c", "Passw0rd!", RoleEnum.CASHIER)
    _create_user("analyst_c", "Passw0rd!", RoleEnum.CREDIT_ANALYST)

    cashier_metadata = _login(auth_stub, "cashier_c", "Passw0rd!")
    with pytest.raises(grpc.RpcError) as exc_info:
        client_stub.CreateClient(_solicitud_alta_cliente(), metadata=cashier_metadata)
    assert exc_info.value.code() == grpc.StatusCode.PERMISSION_DENIED

    analyst_metadata = _login(auth_stub, "analyst_c", "Passw0rd!")
    response = client_stub.CreateClient(
        _solicitud_alta_cliente(), metadata=analyst_metadata
    )
    assert response.client_id


def test_search_clients_still_allows_cashier(stubs):
    """La contracara de BR-CAJA-005: el cajero necesita encontrar al cliente
    para poder informarle cuánto debe."""
    auth_stub, client_stub = stubs
    _create_user("cashier_s", "Passw0rd!", RoleEnum.CASHIER)
    _create_client_row(national_id="5000009", email="lookup@example.com")
    metadata = _login(auth_stub, "cashier_s", "Passw0rd!")

    response = client_stub.SearchClients(
        client_service_pb2.SearchClientsRequest(search_term="5000009"),
        metadata=metadata,
    )
    assert len(response.clients) == 1


def test_deactivate_client_requires_manager_or_above(stubs):
    auth_stub, client_stub = stubs
    _create_user("cashier_d", "Passw0rd!", RoleEnum.CASHIER)
    _create_user("manager_d", "Passw0rd!", RoleEnum.MANAGER)
    client_id = _create_client_row(national_id="5000002", email="deact1@example.com")

    cashier_metadata = _login(auth_stub, "cashier_d", "Passw0rd!")
    with pytest.raises(grpc.RpcError) as exc_info:
        client_stub.DeactivateClient(
            client_service_pb2.DeactivateClientRequest(client_id=str(client_id)),
            metadata=cashier_metadata,
        )
    assert exc_info.value.code() == grpc.StatusCode.PERMISSION_DENIED

    manager_metadata = _login(auth_stub, "manager_d", "Passw0rd!")
    response = client_stub.DeactivateClient(
        client_service_pb2.DeactivateClientRequest(client_id=str(client_id)),
        metadata=manager_metadata,
    )
    assert response.success


def test_update_national_id_requires_admin(stubs):
    auth_stub, client_stub = stubs
    _create_user("manager_n", "Passw0rd!", RoleEnum.MANAGER)
    _create_user("admin_n", "Passw0rd!", RoleEnum.ADMIN)
    client_id = _create_client_row(national_id="5000003", email="natid1@example.com")

    manager_metadata = _login(auth_stub, "manager_n", "Passw0rd!")
    with pytest.raises(grpc.RpcError) as exc_info:
        client_stub.UpdateNationalId(
            client_service_pb2.UpdateNationalIdRequest(
                client_id=str(client_id), new_national_id="5000004"
            ),
            metadata=manager_metadata,
        )
    assert exc_info.value.code() == grpc.StatusCode.PERMISSION_DENIED

    admin_metadata = _login(auth_stub, "admin_n", "Passw0rd!")
    response = client_stub.UpdateNationalId(
        client_service_pb2.UpdateNationalIdRequest(
            client_id=str(client_id), new_national_id="5000004"
        ),
        metadata=admin_metadata,
    )
    assert response.success


def test_search_clients_without_token_is_unauthenticated(stubs):
    _, client_stub = stubs
    with pytest.raises(grpc.RpcError) as exc_info:
        client_stub.SearchClients(
            client_service_pb2.SearchClientsRequest(search_term="")
        )
    assert exc_info.value.code() == grpc.StatusCode.UNAUTHENTICATED
