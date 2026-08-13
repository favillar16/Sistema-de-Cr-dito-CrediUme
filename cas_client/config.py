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
# Optional, only needed if the server requires mutual TLS
# (GRPC_TLS_CLIENT_CA_FILE set on the server) -- this client's own
# certificate/key to present back to the server.
GRPC_TLS_CLIENT_CERT_FILE = os.environ.get("GRPC_TLS_CLIENT_CERT_FILE")
GRPC_TLS_CLIENT_KEY_FILE = os.environ.get("GRPC_TLS_CLIENT_KEY_FILE")
