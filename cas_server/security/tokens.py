import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt

from cas_server import config


@dataclass(frozen=True)
class TokenClaims:
    user_id: str
    username: str
    role: str
    jti: str
    exp: datetime


def create_access_token(
    user_id: str, username: str, role: str
) -> tuple[str, TokenClaims]:
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=config.JWT_EXPIRES_SECONDS)
    jti = str(uuid.uuid4())
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "jti": jti,
        "iat": now,
        "exp": expires_at,
    }
    token = jwt.encode(payload, config.JWT_SECRET_KEY, algorithm=config.JWT_ALGORITHM)
    claims = TokenClaims(
        user_id=user_id, username=username, role=role, jti=jti, exp=expires_at
    )
    return token, claims


def decode_token(token: str) -> TokenClaims:
    """Raises jwt.PyJWTError (or subclasses) on invalid/expired tokens."""
    payload = jwt.decode(
        token, config.JWT_SECRET_KEY, algorithms=[config.JWT_ALGORITHM]
    )
    return TokenClaims(
        user_id=payload["sub"],
        username=payload["username"],
        role=payload["role"],
        jti=payload["jti"],
        exp=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
    )
