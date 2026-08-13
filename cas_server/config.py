import os
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


POSTGRES_USER = _require("POSTGRES_USER")
POSTGRES_PASSWORD = _require("POSTGRES_PASSWORD")
POSTGRES_DB = _require("POSTGRES_DB")
POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "127.0.0.1")
POSTGRES_PORT = os.environ.get("POSTGRES_PORT", "5432")

DATABASE_URL = (
    f"postgresql+psycopg2://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
    f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
)

GRPC_HOST = os.environ.get("GRPC_HOST", "0.0.0.0")
GRPC_PORT = int(os.environ.get("GRPC_PORT", "50051"))

JWT_SECRET_KEY = _require("JWT_SECRET_KEY")
JWT_ALGORITHM = "HS256"
JWT_EXPIRES_SECONDS = 8 * 60 * 60  # BR-AUTH-003: 8 hour session, no refresh tokens

LOCKOUT_MAX_ATTEMPTS = 5  # BR-AUTH-002
LOCKOUT_DURATION_SECONDS = 15 * 60

# Not in specs/authentication/README -- no password-complexity rule existed
# anywhere before the admin-facing user-management UI (CreateUser/ResetPassword
# in auth_service.py) was added, so this is the first one introduced.
PASSWORD_MIN_LENGTH = 8

LOAN_MAX_ACTIVE_PER_CLIENT = 3  # BR-LOAN-001
LOAN_MAX_INSTALLMENT_INCOME_RATIO = Decimal("0.40")  # BR-LOAN-002
LOAN_APPROVAL_EXPIRY_DAYS = 30  # BR-LOAN-003
LOAN_DEFAULT_FIRST_DUE_DAYS = 30  # BR-LOAN-004
LOAN_FIXED_INTEREST_RATE = Decimal("0.24")  # BR-LOAN-007, 24% anual
