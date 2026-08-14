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
    """Se altera el PRIMER carácter de la firma, no el último.

    La versión anterior de este test flipeaba el último carácter entre "A" y
    "B" y fallaba ~7% de las veces: la firma HMAC-SHA256 son 32 bytes = 256
    bits, que en base64url ocupan 43 caracteres, y el último solo aporta 2
    bits significativos (los otros 4 son relleno). "A" (000000) y "B"
    (000001) comparten esos 2 bits, así que para cualquier token cuyo último
    carácter cayera en A-D el cambio decodificaba a los mismos bytes de
    firma, la verificación pasaba y no se lanzaba ninguna excepción. El
    primer carácter, en cambio, aporta sus 6 bits completos, así que
    alterarlo siempre cambia la firma.
    """
    header, payload, signature = token_parts(
        create_access_token(user_id="u1", username="alice", role="ADMIN")[0]
    )
    tampered_signature = ("A" if signature[0] != "A" else "B") + signature[1:]
    tampered = f"{header}.{payload}.{tampered_signature}"
    with pytest.raises(jwt.PyJWTError):
        decode_token(tampered)


def token_parts(token: str) -> tuple[str, str, str]:
    header, payload, signature = token.split(".")
    return header, payload, signature


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
