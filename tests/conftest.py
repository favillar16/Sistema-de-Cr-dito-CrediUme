import os

import pytest
from sqlalchemy import text

from cas_server import config
from cas_server.db.base import SessionLocal, engine

# Escotilla para correr la suite igual, a sabiendas de que borra todo.
VARIABLE_DE_CONFIRMACION = "CAS_ALLOW_DESTRUCTIVE_TESTS"

TABLAS_CON_DATOS_REALES = ("users", "clients", "loans")


def pytest_sessionstart(session: pytest.Session) -> None:
    """Aborta la suite si la base configurada tiene datos reales.

    `clean_db` (abajo) hace TRUNCATE después de CADA test, y `cas_server/.env`
    apunta a la MISMA base en la máquina de despliegue que en desarrollo: en la
    PC servidor, un `pytest` sin más borra los usuarios, clientes y préstamos de
    producción. No es hipotético -- pasó (2026-08-15), y Postgres acá corre sin
    archive_mode, o sea sin forma de recuperar el punto anterior.

    La comprobación es por datos, no por nombre de base: una base de trabajo
    recién truncada por la suite anterior queda vacía y no molesta, mientras que
    cualquier base con usuarios/clientes/préstamos cargados detiene la corrida
    antes del primer test.
    """
    if os.environ.get(VARIABLE_DE_CONFIRMACION, "").strip() in {"1", "true", "True"}:
        return

    with engine.connect() as conn:
        poblada = {
            tabla: conn.execute(text(f"SELECT count(*) FROM {tabla}")).scalar()
            for tabla in TABLAS_CON_DATOS_REALES
        }

    if any(poblada.values()):
        detalle = ", ".join(f"{t}={n}" for t, n in poblada.items())
        raise pytest.UsageError(
            f"La base '{config.POSTGRES_DB}' en {config.POSTGRES_HOST} tiene "
            f"datos ({detalle}) y esta suite hace TRUNCATE después de cada "
            f"test: correrla los borraría sin vuelta atrás.\n"
            f"Opciones:\n"
            f"  - apuntar cas_server/.env a una base de pruebas aparte, o\n"
            f"  - respaldar primero (pg_dump) y correr con "
            f"{VARIABLE_DE_CONFIRMACION}=1.\n"
            f"Para las pruebas que no tocan la base:\n"
            f"  pytest tests/client --noconftest"
        )


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
                "cash_movements, cash_sessions, "
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
