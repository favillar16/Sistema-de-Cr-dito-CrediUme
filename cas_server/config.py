import os
from datetime import date
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
# BR-LOAN-007 (revisado 2026-08-28). Tasa nominal anual: amortization.py
# aplica tasa_anual / 12 por período sobre el **monto original** del préstamo
# (BR-LOAN-013, el interés no varía). 0.20 = 20% anual = el **máximo que la
# ley permite cobrar como interés** -- ya no 45%: eso era interés + gastos
# administrativos mezclados en una sola tasa, y por ley el interés en sí no
# puede superar el 20%. La cláusula de interés compensatorio del
# Pagaré/Contrato (autorizada por la entidad) declara ahora el 1,667%
# mensual que corresponde a esta tasa, no 3,75%. Si cambia esta tasa hay que
# mover también cas_client/rbac_ui.py's FIXED_INTEREST_RATE (no hay fuente
# compartida entre los dos procesos) y revisar el texto de esa cláusula en
# cas_client/documents.py.
LOAN_FIXED_INTEREST_RATE = Decimal("0.20")  # 20% anual, tope legal de interés

# BR-LOAN-006 (revisado 2026-09-08). Tope conjunto de los 4 cargos
# financiados (impuesto s/intereses, gastos administrativos por desembolso,
# seguro de cancelación, seguros contratados): 40% anual del **capital
# solicitado**, prorrateado por el plazo con la misma mecánica que el interés
# (`capital * LOAN_MAX_CHARGES_RATIO / 12 * term_months`, ver
# loan_service.py's `_tope_cargos`). Es el complemento de
# LOAN_FIXED_INTEREST_RATE: 20% de interés legal + hasta 40% de gastos
# administrativos = el 60% anual que la entidad fija como costo total del
# crédito, sin que el interés en sí supere el máximo legal. Subió de 25% a
# 40% (era 20%+25%=45%) por decisión del negocio -- el Pagaré/Contrato siguen
# declarando el monto real (capital + cargos + interés) sin recortar ni
# ocultar ninguna cifra: lo que cambia es sólo el tope, no la transparencia
# del documento firmado.
LOAN_MAX_CHARGES_RATIO = Decimal("0.40")  # 40% anual, tope de cargos financiados

# BR-LOAN-007/006 (revisado 2026-09-22). Piso de plazo para **cobrar**, no
# para aceptar: un préstamo más corto que un año se cobra como si durara un
# año. Sin esto el prorrateo por plazo hacía que 6 meses rindieran la mitad
# que 12 (10% de interés y 20% de techo de cargos), y la entidad decidió que
# ningún plazo rinda menos que uno de 12 meses.
#
# Consecuencia deliberada: la tasa **guardada** de un préstamo corto ya no es
# LOAN_FIXED_INTEREST_RATE. Un préstamo a 6 meses se crea a 0,40 anual (3,33%
# mensual) para que su interés total sea el mismo 20% del financiado que uno
# a 12, y el Pagaré/Contrato declaran ese mensual porque lo derivan de la
# tasa del propio préstamo. El plazo sigue siendo libre (cualquier entero
# positivo); esto sólo cambia lo que se cobra, no lo que se acepta.
LOAN_RATE_MIN_TERM_MONTHS = 12

# BR-LOAN-017 (2026-09-22). Interés moratorio: una sola tasa con carácter
# moratorio y punitorio sobre **cada cuota vencida e impaga**, tal como ya lo
# declaraban el Pagaré y el Contrato desde 2026-09-02 sin que nadie lo
# calculara. Los tres valores son los mismos que imprime la cláusula
# (cas_client/documents.py's _TERM_MORATORY_*), espejados a mano como el
# resto de las constantes comerciales: si divergen, el papel firmado y lo que
# se cobra dicen cosas distintas.
LOAN_LATE_FEE_MONTHLY_RATE = Decimal("0.0038")  # 0,38% mensual
LOAN_LATE_FEE_GRACE_DAYS = 5  # días corridos desde el vencimiento
LOAN_LATE_FEE_DAYS_PER_MONTH = 30  # base de prorrateo diario

# Fecha desde la cual se devenga mora, cualquiera sea el vencimiento de la
# cuota. La mora se deriva de fechas y no se persiste, así que sin este piso
# el día del despliegue toda la cartera vencida aparecería debiendo meses de
# recargo que la entidad nunca cobró. Decisión de negocio: la mora corre
# desde la puesta en marcha en adelante. Se puede mover por .env.
# El default va aparte del valor efectivo a propósito: es lo que se despliega
# y lo único que tiene sentido proteger con un test. El override por entorno
# existe para poder *probar* la mora antes de esa fecha (si no, en un equipo
# de desarrollo siempre da cero), y un override local no debe romper la suite.
LOAN_LATE_FEE_DEFAULT_START_DATE = date(2026, 10, 1)
LOAN_LATE_FEE_START_DATE = date.fromisoformat(
    os.environ.get(
        "LOAN_LATE_FEE_START_DATE", LOAN_LATE_FEE_DEFAULT_START_DATE.isoformat()
    )
)
