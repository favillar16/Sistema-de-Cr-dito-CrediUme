import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

GRPC_SERVER_HOST = os.environ.get("GRPC_SERVER_HOST", "127.0.0.1")
GRPC_PORT = int(os.environ.get("GRPC_PORT", "50051"))
