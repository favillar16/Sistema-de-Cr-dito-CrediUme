from datetime import datetime, timedelta, timezone

import jwt
import pytest

from cas_server import config
from cas_server.security.tokens import create_access_token, decode_token


def test_create_and_decode_roundtrip():
    token, claims = create_access_token(user_id="u1", username="alice", role="ADMIN")
    decoded = decode_token(token)
    assert decoded.user_id == "u1"
    assert decoded.username == "alice"
    assert decoded.role == "ADMIN"
    assert decoded.jti == claims.jti


def test_decode_rejects_tampered_signature():
    token, _ = create_access_token(user_id="u1", username="alice", role="ADMIN")
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(jwt.PyJWTError):
        decode_token(tampered)


def test_decode_rejects_expired_token():
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "u1",
        "username": "alice",
        "role": "ADMIN",
        "jti": "fixed-jti",
        "iat": now - timedelta(hours=9),
        "exp": now - timedelta(hours=1),
    }
    expired_token = jwt.encode(
        payload, config.JWT_SECRET_KEY, algorithm=config.JWT_ALGORITHM
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(expired_token)
