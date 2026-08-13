import threading
import uuid

import auth_service_pb2
import grpc
import pytest

from cas_server.db.base import SessionLocal
from cas_server.db.models import RevokedToken, RoleEnum, User
from cas_server.security.passwords import hash_password
from cas_server.security.tokens import decode_token
from cas_server.services.auth_service import AuthServicer


class AbortCalled(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class FakeContext:
    def __init__(self, peer="ipv4:127.0.0.1:1234"):
        self._peer = peer

    def abort(self, code, message):
        raise AbortCalled(code, message)

    def peer(self):
        return self._peer


def _create_user(username="cashier1", password="Passw0rd!", role=RoleEnum.CASHIER):
    with SessionLocal() as session:
        user = User(username=username, password_hash=hash_password(password), role=role)
        session.add(user)
        session.commit()
        session.refresh(user)
        return user.id


@pytest.fixture
def servicer():
    return AuthServicer()


def test_login_success_issues_token(servicer):
    _create_user(username="alice", password="Passw0rd!")
    response = servicer.Login(
        auth_service_pb2.LoginRequest(username="alice", password="Passw0rd!"),
        FakeContext(),
    )
    assert response.access_token
    assert response.role == "CASHIER"
    assert response.expires_in_seconds > 0


def test_login_wrong_password_is_unauthenticated(servicer):
    _create_user(username="bob", password="Passw0rd!")
    with pytest.raises(AbortCalled) as exc_info:
        servicer.Login(
            auth_service_pb2.LoginRequest(username="bob", password="wrong"),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.UNAUTHENTICATED


def test_login_unknown_username_is_unauthenticated(servicer):
    with pytest.raises(AbortCalled) as exc_info:
        servicer.Login(
            auth_service_pb2.LoginRequest(username="ghost", password="whatever"),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.UNAUTHENTICATED


def test_login_locks_account_after_five_failed_attempts(servicer):
    _create_user(username="carol", password="Passw0rd!")

    for _ in range(5):
        with pytest.raises(AbortCalled):
            servicer.Login(
                auth_service_pb2.LoginRequest(username="carol", password="wrong"),
                FakeContext(),
            )

    # BR-AUTH-002: even the correct password is rejected once locked.
    with pytest.raises(AbortCalled) as exc_info:
        servicer.Login(
            auth_service_pb2.LoginRequest(username="carol", password="Passw0rd!"),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.UNAUTHENTICATED
    assert "locked" in exc_info.value.message.lower()

    with SessionLocal() as session:
        user = session.query(User).filter_by(username="carol").one()
        assert user.is_locked
        assert user.locked_until is not None


def test_logout_revokes_token(servicer):
    _create_user(username="dave", password="Passw0rd!")
    login_response = servicer.Login(
        auth_service_pb2.LoginRequest(username="dave", password="Passw0rd!"),
        FakeContext(),
    )
    logout_response = servicer.Logout(
        auth_service_pb2.LogoutRequest(access_token=login_response.access_token),
        FakeContext(),
    )
    assert logout_response.success

    claims = decode_token(login_response.access_token)
    with SessionLocal() as session:
        assert session.get(RevokedToken, claims.jti) is not None


def test_logout_rejects_malformed_token(servicer):
    with pytest.raises(AbortCalled) as exc_info:
        servicer.Logout(
            auth_service_pb2.LogoutRequest(access_token="not-a-jwt"), FakeContext()
        )
    assert exc_info.value.code == grpc.StatusCode.INVALID_ARGUMENT


def test_reset_password_unknown_user_is_not_found(servicer):
    with pytest.raises(AbortCalled) as exc_info:
        servicer.ResetPassword(
            auth_service_pb2.ResetPasswordRequest(
                target_user_id=str(uuid.uuid4()), new_password="NewPassw0rd!"
            ),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.NOT_FOUND


def test_reset_password_rejects_malformed_uuid(servicer):
    with pytest.raises(AbortCalled) as exc_info:
        servicer.ResetPassword(
            auth_service_pb2.ResetPasswordRequest(
                target_user_id="not-a-uuid", new_password="NewPassw0rd!"
            ),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.INVALID_ARGUMENT


def test_reset_password_updates_hash_and_unlocks(servicer):
    user_id = _create_user(username="erin", password="OldPassw0rd!")

    with SessionLocal() as session:
        user = session.get(User, user_id)
        user.is_locked = True
        user.failed_login_attempts = 5
        session.commit()

    servicer.ResetPassword(
        auth_service_pb2.ResetPasswordRequest(
            target_user_id=str(user_id), new_password="NewPassw0rd!"
        ),
        FakeContext(),
    )

    login_response = servicer.Login(
        auth_service_pb2.LoginRequest(username="erin", password="NewPassw0rd!"),
        FakeContext(),
    )
    assert login_response.access_token


def test_reset_password_rejects_short_password(servicer):
    user_id = _create_user(username="fiona", password="OldPassw0rd!")

    with pytest.raises(AbortCalled) as exc_info:
        servicer.ResetPassword(
            auth_service_pb2.ResetPasswordRequest(
                target_user_id=str(user_id), new_password="short"
            ),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.INVALID_ARGUMENT


def test_create_user_success_sets_role(servicer):
    response = servicer.CreateUser(
        auth_service_pb2.CreateUserRequest(
            username="new_manager", password="Passw0rd!", role="MANAGER"
        ),
        FakeContext(),
    )
    assert response.user_id
    assert response.username == "new_manager"
    assert response.role == "MANAGER"

    with SessionLocal() as session:
        user = session.query(User).filter_by(username="new_manager").one()
        assert user.role == RoleEnum.MANAGER

    login_response = servicer.Login(
        auth_service_pb2.LoginRequest(username="new_manager", password="Passw0rd!"),
        FakeContext(),
    )
    assert login_response.role == "MANAGER"


def test_create_user_duplicate_username_is_already_exists(servicer):
    _create_user(username="dup_user", password="Passw0rd!")

    with pytest.raises(AbortCalled) as exc_info:
        servicer.CreateUser(
            auth_service_pb2.CreateUserRequest(
                username="dup_user", password="Passw0rd!", role="CASHIER"
            ),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.ALREADY_EXISTS


def test_create_user_concurrent_duplicate_username_never_both_succeed(servicer):
    """ES-006 §3.1: two concurrent CreateUser calls for the same username
    must never both succeed, and the one that loses the race gets
    ALREADY_EXISTS -- not an uncaught IntegrityError leaking as a generic
    INTERNAL/UNKNOWN. Runs two real threads against the real DB, unlike the
    sequential test above."""
    results = []
    errors = []
    barrier = threading.Barrier(2)

    def worker() -> None:
        barrier.wait()
        try:
            response = servicer.CreateUser(
                auth_service_pb2.CreateUserRequest(
                    username="race_user", password="Passw0rd!", role="CASHIER"
                ),
                FakeContext(),
            )
            results.append(response)
        except AbortCalled as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 1
    assert len(errors) == 1
    assert errors[0].code == grpc.StatusCode.ALREADY_EXISTS


def test_create_user_invalid_role_is_invalid_argument(servicer):
    with pytest.raises(AbortCalled) as exc_info:
        servicer.CreateUser(
            auth_service_pb2.CreateUserRequest(
                username="bad_role_user", password="Passw0rd!", role="SUPERUSER"
            ),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.INVALID_ARGUMENT


def test_create_user_short_password_is_invalid_argument(servicer):
    with pytest.raises(AbortCalled) as exc_info:
        servicer.CreateUser(
            auth_service_pb2.CreateUserRequest(
                username="short_pw_user", password="short", role="CASHIER"
            ),
            FakeContext(),
        )
    assert exc_info.value.code == grpc.StatusCode.INVALID_ARGUMENT


def test_list_users_returns_created_users(servicer):
    _create_user(username="listed_one", password="Passw0rd!", role=RoleEnum.CASHIER)
    _create_user(username="listed_two", password="Passw0rd!", role=RoleEnum.ADMIN)

    response = servicer.ListUsers(auth_service_pb2.ListUsersRequest(), FakeContext())

    usernames = {user.username: user for user in response.users}
    assert "listed_one" in usernames
    assert "listed_two" in usernames
    assert usernames["listed_one"].role == "CASHIER"
    assert usernames["listed_two"].role == "ADMIN"
    assert usernames["listed_one"].is_locked is False
    assert not usernames["listed_one"].HasField("last_login")


def test_list_users_sets_last_login_after_login(servicer):
    _create_user(username="logs_in", password="Passw0rd!")
    servicer.Login(
        auth_service_pb2.LoginRequest(username="logs_in", password="Passw0rd!"),
        FakeContext(),
    )

    response = servicer.ListUsers(auth_service_pb2.ListUsersRequest(), FakeContext())
    entry = next(user for user in response.users if user.username == "logs_in")
    assert entry.HasField("last_login")
