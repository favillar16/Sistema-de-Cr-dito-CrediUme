import pytest
from sqlalchemy import text

from cas_server.db.base import SessionLocal, engine


@pytest.fixture(autouse=True)
def clean_db():
    """Run each test against the real local Postgres schema, then wipe it.

    The models use Postgres-native UUID/Enum types, so sqlite substitutes
    aren't a faithful stand-in -- this truncates the auth tables after every
    test instead of relying on transactional rollback (simpler, and the
    service layer manages its own SessionLocal()/commit() calls internally).
    """
    yield
    with engine.connect() as conn:
        conn.execute(
            text(
                "TRUNCATE TABLE audit_logs, revoked_tokens, users, "
                "loan_payments, loan_installment_adjustments, loans, clients "
                "RESTART IDENTITY CASCADE"
            )
        )
        conn.commit()


@pytest.fixture
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
