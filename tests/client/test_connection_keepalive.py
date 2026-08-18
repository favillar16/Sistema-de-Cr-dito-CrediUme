"""El par de ajustes de keepalive cliente/servidor, y el estado de conexión.

Lo que se protege acá no es "que exista keepalive" sino que los dos lados
**sigan siendo compatibles entre sí**. Es una relación entre dos archivos de
configuración de dos procesos distintos, del tipo que se rompe en silencio
cuando alguien afina un solo lado -- igual que FIXED_INTEREST_RATE contra
LOAN_FIXED_INTEREST_RATE, que ya tiene su propia guarda por el mismo motivo.

Y romperla no degrada el sistema: lo empeora respecto de no tener keepalive.
Un cliente que hace ping más seguido de lo que el servidor tolera recibe
GOAWAY con ENHANCE_YOUR_CALM y el servidor le corta la conexión a propósito,
que es exactamente el síntoma que el keepalive venía a resolver.
"""

import grpc
import pytest

from cas_client import config as client_config
from cas_client.grpc_client import _channel_options
from cas_client.widgets.async_worker import _is_unreachable
from cas_client.grpc_client import ApiError, AuthError
from cas_server import config as server_config


def _as_dict(options) -> dict:
    return dict(options)


def test_client_pings_no_faster_than_the_server_tolerates():
    """La invariante que hace que todo esto funcione en vez de contraproducir.

    gRPC cuenta los PING que llegan sin tráfico de datos de por medio; si
    llegan más seguido que `min_ping_interval_without_data_ms`, responde
    GOAWAY/ENHANCE_YOUR_CALM. El default del servidor son 300000 ms (5 min),
    o sea que un cliente con keepalive de 30 s contra un servidor sin
    configurar sería desconectado por el propio servidor.
    """
    intervalo_cliente = _as_dict(_channel_options())["grpc.keepalive_time_ms"]
    tolerancia_servidor = _as_dict(server_config.grpc_keepalive_options())[
        "grpc.http2.min_ping_interval_without_data_ms"
    ]
    assert tolerancia_servidor <= intervalo_cliente, (
        f"el cliente hace ping cada {intervalo_cliente} ms pero el servidor "
        f"sólo tolera uno cada {tolerancia_servidor} ms: cortaría la conexión "
        "con ENHANCE_YOUR_CALM"
    )


@pytest.mark.parametrize(
    "options_fn",
    [_channel_options, server_config.grpc_keepalive_options],
    ids=["cliente", "servidor"],
)
def test_keepalive_runs_while_idle_on_both_sides(options_fn):
    """`permit_without_calls` es la opción que hace útil a todo el resto: la
    conexión se cae justamente cuando NO hay RPC en vuelo (es cuando el router
    descarta la entrada NAT), así que un keepalive que sólo corriera durante
    las llamadas no evitaría nada."""
    opciones = _as_dict(options_fn())
    assert opciones["grpc.keepalive_permit_without_calls"] == 1
    assert opciones["grpc.http2.max_pings_without_data"] == 0


def test_keepalive_detects_a_dead_link_well_within_the_call_timeout():
    """El plazo por llamada tiene que dar tiempo a que el keepalive note la
    caída y el canal se reconecte; si fuera al revés, la llamada expiraría
    antes de que la reconexión automática llegue a servir de algo."""
    opciones = _as_dict(_channel_options())
    deteccion_segundos = (
        opciones["grpc.keepalive_time_ms"] + opciones["grpc.keepalive_timeout_ms"]
    ) / 1000
    assert deteccion_segundos < client_config.GRPC_CALL_TIMEOUT_SECONDS * 3


def test_every_channel_carries_the_keepalive_options(monkeypatch):
    """Las opciones tienen que ir en _create_channel y no en cada cliente: son
    cinco canales (Auth/Client/Loan/Dashboard/Cash) y uno solo sin keepalive
    seguiría muriendo en silencio."""
    from cas_client import config as cfg
    from cas_client.grpc_client import _create_channel

    monkeypatch.setattr(cfg, "GRPC_TLS_CA_FILE", None)
    capturado = {}
    original = grpc.insecure_channel

    def espia(target, options=None, **kwargs):
        capturado["options"] = _as_dict(options or [])
        return original(target, options=options, **kwargs)

    monkeypatch.setattr(grpc, "insecure_channel", espia)
    canal = _create_channel("127.0.0.1:50051")
    try:
        assert capturado["options"]["grpc.keepalive_permit_without_calls"] == 1
        assert "grpc.keepalive_time_ms" in capturado["options"]
    finally:
        canal.close()


# ---- Traducción del corte de enlace --------------------------------------


@pytest.mark.parametrize("error_cls", [ApiError, AuthError])
@pytest.mark.parametrize(
    "code",
    [grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED],
)
def test_network_failures_are_reported_as_a_lost_connection(error_cls, code):
    """DEADLINE_EXCEEDED sólo pasó a ser posible cuando las llamadas ganaron
    un plazo; ningún _friendly_message() de las vistas lo contempla, así que
    sin este manejo central caería en "Ocurrió un error inesperado"."""
    assert _is_unreachable(error_cls(code, "boom")) is True


@pytest.mark.parametrize(
    "code",
    [
        # No es un problema de red: es la sesión vencida, que tiene su propio
        # camino (session_events.expired) y no debe confundirse con un corte.
        grpc.StatusCode.UNAUTHENTICATED,
        grpc.StatusCode.FAILED_PRECONDITION,
        grpc.StatusCode.INVALID_ARGUMENT,
        grpc.StatusCode.PERMISSION_DENIED,
        grpc.StatusCode.NOT_FOUND,
    ],
)
def test_business_errors_are_not_mistaken_for_a_lost_connection(code):
    assert _is_unreachable(ApiError(code, "boom")) is False
