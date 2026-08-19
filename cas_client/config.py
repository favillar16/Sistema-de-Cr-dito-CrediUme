import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

GRPC_SERVER_HOST = os.environ.get("GRPC_SERVER_HOST", "127.0.0.1")
GRPC_PORT = int(os.environ.get("GRPC_PORT", "50051"))

# Mirrors cas_server/config.py's TLS opt-in. Unset (default) = insecure
# channel, same as before. Set GRPC_TLS_CA_FILE to a CA certificate (or the
# server's own cert, if self-signed) to trust the server's identity over
# TLS -- see cas_client/grpc_client.py's _create_channel().
GRPC_TLS_CA_FILE = os.environ.get("GRPC_TLS_CA_FILE")
# Escape hatch para desarrollo contra un servidor remoto SIN TLS. Por defecto
# (sin definir) el cliente se niega a hablar en texto plano con un host que no
# sea local, en vez de degradarse en silencio y fallar después como si fuera un
# problema de red -- ver _create_channel() en grpc_client.py.
GRPC_ALLOW_INSECURE = os.environ.get("GRPC_ALLOW_INSECURE", "").strip().lower() in {
    "1",
    "true",
    "si",
    "sí",
    "yes",
}
# Optional, only needed if the server requires mutual TLS
# (GRPC_TLS_CLIENT_CA_FILE set on the server) -- this client's own
# certificate/key to present back to the server.
GRPC_TLS_CLIENT_CERT_FILE = os.environ.get("GRPC_TLS_CLIENT_CERT_FILE")
GRPC_TLS_CLIENT_KEY_FILE = os.environ.get("GRPC_TLS_CLIENT_KEY_FILE")

# ---- Salud de la conexión (keepalive) ------------------------------------
#
# El cliente abre sus canales al arrancar y los sostiene toda la sesión (el
# JWT dura 8 horas, BR-AUTH-003), pero entre acción y acción la conexión queda
# ociosa largos ratos. En la LAN real (Wi-Fi + router doméstico) esa conexión
# ociosa se cae sola: la tabla NAT/firewall del router descarta el flujo TCP y
# el ahorro de energía del adaptador Wi-Fi de Windows hace lo suyo. Ninguno de
# los dos extremos se entera -- el socket sigue "abierto" para ambos, y el
# operador sólo descubre que se cortó cuando aprieta un botón y la llamada se
# cuelga esperando el timeout de retransmisión de TCP.
#
# El PING de HTTP/2 que habilitan estas opciones es lo que evita las dos cosas
# a la vez: mantiene viva la entrada NAT del router, y le da a gRPC una señal
# propia de vida con la que detectar la caída en ~40 s y reconectar solo, en
# vez de descubrirla recién en la próxima llamada del usuario.
#
# IMPORTANTE: estos valores tienen que ir de la mano con los de
# cas_server/config.py. Un cliente que hace ping más seguido de lo que el
# servidor tolera recibe GOAWAY/ENHANCE_YOUR_CALM y el servidor le corta la
# conexión a propósito -- es decir, "arreglar" sólo este lado empeora el
# problema en vez de resolverlo.
GRPC_KEEPALIVE_TIME_MS = int(os.environ.get("GRPC_KEEPALIVE_TIME_MS", "30000"))
GRPC_KEEPALIVE_TIMEOUT_MS = int(os.environ.get("GRPC_KEEPALIVE_TIMEOUT_MS", "10000"))

# Techo por llamada. Sin esto una RPC contra un servidor inalcanzable se queda
# esperando para siempre y dejaba al AsyncWorker colgado con la barra de
# progreso girando y sin forma de cancelar. 20 s es holgado para consultas de
# LAN contra Postgres y corto como para que el usuario reciba un error claro.
GRPC_CALL_TIMEOUT_SECONDS = float(os.environ.get("GRPC_CALL_TIMEOUT_SECONDS", "20"))
