"""RBAC coverage for DashboardService, mirroring test_interceptor_integration.py's
pattern: a real grpc.Server with AuthInterceptor wired in, driven over an
actual channel so metadata-based auth is exercised for real."""

from concurrent import futures

import auth_service_pb2
import auth_service_pb2_grpc
import dashboard_service_pb2
import dashboard_service_pb2_grpc
import grpc
import pytest

from cas_server.db.base import SessionLocal
from cas_server.db.models import RoleEnum, User
from cas_server.security.interceptor import AuthInterceptor
from cas_server.security.passwords import hash_password
from cas_server.services.auth_service import AuthServicer
from cas_server.services.dashboard_service import DashboardServicer


@pytest.fixture
def stubs():
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=4), interceptors=[AuthInterceptor()]
    )
    auth_service_pb2_grpc.add_AuthServiceServicer_to_server(AuthServicer(), server)
    dashboard_service_pb2_grpc.add_DashboardServiceServicer_to_server(
        DashboardServicer(), server
    )
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()

    channel = grpc.insecure_channel(f"127.0.0.1:{port}")
    auth_stub = auth_service_pb2_grpc.AuthServiceStub(channel)
    dashboard_stub = dashboard_service_pb2_grpc.DashboardServiceStub(channel)
    try:
        yield auth_stub, dashboard_stub
    finally:
        channel.close()
        server.stop(grace=None)


def _create_user(username, password, role):
    with SessionLocal() as session:
        session.add(
            User(username=username, password_hash=hash_password(password), role=role)
        )
        session.commit()


def _login(auth_stub, username, password):
    response = auth_stub.Login(
        auth_service_pb2.LoginRequest(username=username, password=password)
    )
    return (("authorization", f"Bearer {response.access_token}"),)


def test_get_dashboard_stats_allows_every_role(stubs):
    auth_stub, dashboard_stub = stubs
    for role in RoleEnum:
        username = f"dash_{role.value.lower()}"
        _create_user(username, "Passw0rd!", role)
        metadata = _login(auth_stub, username, "Passw0rd!")
        response = dashboard_stub.GetDashboardStats(
            dashboard_service_pb2.GetDashboardStatsRequest(), metadata=metadata
        )
        assert response.total_clients_count == 0


def test_get_dashboard_stats_without_token_is_unauthenticated(stubs):
    _, dashboard_stub = stubs
    with pytest.raises(grpc.RpcError) as exc_info:
        dashboard_stub.GetDashboardStats(
            dashboard_service_pb2.GetDashboardStatsRequest()
        )
    assert exc_info.value.code() == grpc.StatusCode.UNAUTHENTICATED
