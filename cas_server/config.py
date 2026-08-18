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

# TLS is opt-in (ES-004 defers it to an explicit deployment decision, not a
# hard requirement of this codebase) -- unset, the server keeps binding an
# insecure port exactly like before. Set both to enable server-side TLS;
# see cas_server/server.py's _build_server_credentials().
GRPC_TLS_CERT_FILE = os.environ.get("GRPC_TLS_CERT_FILE")
GRPC_TLS_KEY_FILE = os.environ.get("GRPC_TLS_KEY_FILE")
# Optional, only meaningful together with the two above: if set, the server
# requires and verifies client certificates signed by this CA (mutual TLS)
# instead of plain server-side TLS.
GRPC_TLS_CLIENT_CA_FILE = os.environ.get("GRPC_TLS_CLIENT_CA_FILE")

# ---- Salud de la conexión (keepalive) ------------------------------------
#
# Contraparte obligatoria del bloque homónimo en cas_client/config.py -- ver
# ahí la explicación de por qué una conexión ociosa se cae sola en la LAN.
#
# El valor que importa acá es GRPC_MIN_PING_INTERVAL_WITHOUT_DATA_MS: por
# defecto gRPC lo fija en 300000 (5 min) y, si el cliente hace ping más
# seguido que eso sin tráfico de por medio, el servidor considera el ping
# abusivo y responde GOAWAY/ENHANCE_YOUR_CALM, cortando la conexión. Es decir,
# con el default un cliente con keepalive de 30 s sería desconectado
# activamente por el servidor. Se baja por debajo del intervalo del cliente
# para que los pings que mantienen viva la conexión sean aceptados.
GRPC_KEEPALIVE_TIME_MS = int(os.environ.get("GRPC_KEEPALIVE_TIME_MS", "30000"))
GRPC_KEEPALIVE_TIMEOUT_MS = int(os.environ.get("GRPC_KEEPALIVE_TIMEOUT_MS", "10000"))
GRPC_MIN_PING_INTERVAL_WITHOUT_DATA_MS = int(
    os.environ.get("GRPC_MIN_PING_INTERVAL_WITHOUT_DATA_MS", "10000")
)


def grpc_keepalive_options() -> list[tuple[str, int]]:
    """Opciones de canal del servidor para keepalive. `permit_without_calls`
    es imprescindible: la caja pasa la mayor parte del turno sin RPC en vuelo,
    que es justamente cuando el router descarta la conexión ociosa, así que un
    keepalive que sólo funcione durante las llamadas no serviría de nada."""
    return [
        ("grpc.keepalive_time_ms", GRPC_KEEPALIVE_TIME_MS),
        ("grpc.keepalive_timeout_ms", GRPC_KEEPALIVE_TIMEOUT_MS),
        ("grpc.keepalive_permit_without_calls", 1),
        ("grpc.http2.max_pings_without_data", 0),
        (
            "grpc.http2.min_ping_interval_without_data_ms",
            GRPC_MIN_PING_INTERVAL_WITHOUT_DATA_MS,
        ),
    ]


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
# BR-LOAN-007. Tasa nominal anual: amortization.py aplica tasa_anual / 12 por
# período, así que 0.18 = 18% anual = el 1,5% mensual sobre saldos deudores que
# fija la cláusula de interés compensatorio del Pagaré/Contrato (autorizado por
# la entidad). Si cambia esta tasa hay que mover también cas_client/rbac_ui.py's
# FIXED_INTEREST_RATE (no hay fuente compartida entre los dos procesos) y
# revisar el texto de esa cláusula en cas_client/documents.py.
LOAN_FIXED_INTEREST_RATE = Decimal("0.18")  # 18% anual = 1,5% mensual
