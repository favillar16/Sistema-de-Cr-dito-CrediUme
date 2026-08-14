"""End-to-end RBAC coverage (BR-AUTH-004): spins up a real grpc.Server with
AuthInterceptor wired in, exactly like cas_server/server.py, and drives it
through an actual grpc channel so metadata-based auth is exercised for real
rather than through AuthServicer method calls directly.
"""

from concurrent import futures

import auth_service_pb2
import auth_service_pb2_grpc
import grpc
import pytest

from cas_server.db.base import SessionLocal
from cas_server.db.models import RoleEnum, User
from cas_server.security.interceptor import AuthInterceptor
from cas_server.security.passwords import hash_password
from cas_server.services.auth_service import AuthServicer


@pytest.fixture
def grpc_stub():
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=4), interceptors=[AuthInterceptor()]
    )
    auth_service_pb2_grpc.add_AuthServiceServicer_to_server(AuthServicer(), server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()

    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    stub = auth_service_pb2_grpc.AuthServiceStub(channel)
    try:
        yield stub
    finally:
        channel.close()
        server.stop(grace=None)


def _create_user(username, password, role):
    with SessionLocal() as session:
        session.add(
            User(username=username, password_hash=hash_password(password), role=role)
        )
        session.commit()


def test_login_requires_no_token(grpc_stub):
    _create_user("frank", "Passw0rd!", RoleEnum.CASHIER)
    response = grpc_stub.Login(
        auth_service_pb2.LoginRequest(username="frank", password="Passw0rd!")
    )
    assert response.access_token


def test_logout_without_token_is_unauthenticated(grpc_stub):
    with pytest.raises(grpc.RpcError) as exc_info:
        grpc_stub.Logout(auth_service_pb2.LogoutRequest(access_token="whatever"))
    assert exc_info.value.code() == grpc.StatusCode.UNAUTHENTICATED


def test_non_admin_cannot_call_reset_password(grpc_stub):
    _create_user("greg", "Passw0rd!", RoleEnum.CASHIER)
    login = grpc_stub.Login(
        auth_service_pb2.LoginRequest(username="greg", password="Passw0rd!")
    )

    with pytest.raises(grpc.RpcError) as exc_info:
        grpc_stub.ResetPassword(
            auth_service_pb2.ResetPasswordRequest(
                target_user_id="00000000-0000-0000-0000-000000000000",
                new_password="NewPassw0rd!",
            ),
            metadata=(("authorization", f"Bearer {login.access_token}"),),
        )
    assert exc_info.value.code() == grpc.StatusCode.PERMISSION_DENIED


def test_admin_can_call_reset_password(grpc_stub):
    _create_user("admin_it", "Passw0rd!", RoleEnum.ADMIN)
    _create_user("target_user", "OldPassw0rd!", RoleEnum.CASHIER)

    admin_login = grpc_stub.Login(
        auth_service_pb2.LoginRequest(username="admin_it", password="Passw0rd!")
    )
    with SessionLocal() as session:
        target_id = session.query(User).filter_by(username="target_user").one().id

    response = grpc_stub.ResetPassword(
        auth_service_pb2.ResetPasswordRequest(
            target_user_id=str(target_id), new_password="NewPassw0rd!"
        ),
        metadata=(("authorization", f"Bearer {admin_login.access_token}"),),
    )
    assert response.success


def test_non_admin_cannot_call_create_user(grpc_stub):
    _create_user("ivan", "Passw0rd!", RoleEnum.CASHIER)
    login = grpc_stub.Login(
        auth_service_pb2.LoginRequest(username="ivan", password="Passw0rd!")
    )

    with pytest.raises(grpc.RpcError) as exc_info:
        grpc_stub.CreateUser(
            auth_service_pb2.CreateUserRequest(
                username="new_from_cashier", password="Passw0rd!", role="CASHIER"
            ),
            metadata=(("authorization", f"Bearer {login.access_token}"),),
        )
    assert exc_info.value.code() == grpc.StatusCode.PERMISSION_DENIED


def test_admin_can_call_create_user(grpc_stub):
    _create_user("julia_admin", "Passw0rd!", RoleEnum.ADMIN)
    login = grpc_stub.Login(
        auth_service_pb2.LoginRequest(username="julia_admin", password="Passw0rd!")
    )

    response = grpc_stub.CreateUser(
        auth_service_pb2.CreateUserRequest(
            username="new_from_admin",
            password="Passw0rd!",
            role="CREDIT_ANALYST",
            first_name="Nuevo",
            last_name="Operador",
            national_id="5123456",
        ),
        metadata=(("authorization", f"Bearer {login.access_token}"),),
    )
    assert response.username == "new_from_admin"


def test_non_admin_cannot_call_list_users(grpc_stub):
    _create_user("kevin", "Passw0rd!", RoleEnum.MANAGER)
    login = grpc_stub.Login(
        auth_service_pb2.LoginRequest(username="kevin", password="Passw0rd!")
    )

    with pytest.raises(grpc.RpcError) as exc_info:
        grpc_stub.ListUsers(
            auth_service_pb2.ListUsersRequest(),
            metadata=(("authorization", f"Bearer {login.access_token}"),),
        )
    assert exc_info.value.code() == grpc.StatusCode.PERMISSION_DENIED


def test_admin_can_call_list_users(grpc_stub):
    _create_user("laura_admin", "Passw0rd!", RoleEnum.ADMIN)
    login = grpc_stub.Login(
        auth_service_pb2.LoginRequest(username="laura_admin", password="Passw0rd!")
    )

    response = grpc_stub.ListUsers(
        auth_service_pb2.ListUsersRequest(),
        metadata=(("authorization", f"Bearer {login.access_token}"),),
    )
    assert any(user.username == "laura_admin" for user in response.users)


def test_revoked_token_is_rejected(grpc_stub):
    _create_user("hana", "Passw0rd!", RoleEnum.MANAGER)
    login = grpc_stub.Login(
        auth_service_pb2.LoginRequest(username="hana", password="Passw0rd!")
    )

    grpc_stub.Logout(
        auth_service_pb2.LogoutRequest(access_token=login.access_token),
        metadata=(("authorization", f"Bearer {login.access_token}"),),
    )

    with pytest.raises(grpc.RpcError) as exc_info:
        grpc_stub.Logout(
            auth_service_pb2.LogoutRequest(access_token=login.access_token),
            metadata=(("authorization", f"Bearer {login.access_token}"),),
        )
    assert exc_info.value.code() == grpc.StatusCode.UNAUTHENTICATED
