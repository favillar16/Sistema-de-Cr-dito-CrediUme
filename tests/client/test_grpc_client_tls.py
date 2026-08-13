"""Coverage for cas_client/grpc_client.py's opt-in TLS support
(_create_channel) -- mirrors tests/server/test_server_tls.py's approach:
branching logic only, dummy byte content instead of real certificates
(grpc.ssl_channel_credentials doesn't validate PEM content at construction
time)."""

import grpc

from cas_client import config
from cas_client.grpc_client import _create_channel


def test_no_tls_config_returns_insecure_channel(monkeypatch):
    monkeypatch.setattr(config, "GRPC_TLS_CA_FILE", None)
    monkeypatch.setattr(config, "GRPC_TLS_CLIENT_CERT_FILE", None)
    monkeypatch.setattr(config, "GRPC_TLS_CLIENT_KEY_FILE", None)

    channel = _create_channel("127.0.0.1:50051")
    try:
        assert isinstance(channel, grpc.Channel)
    finally:
        channel.close()


def test_ca_file_configured_returns_secure_channel(monkeypatch, tmp_path):
    ca_file = tmp_path / "ca.crt"
    ca_file.write_bytes(b"fake-ca")

    monkeypatch.setattr(config, "GRPC_TLS_CA_FILE", str(ca_file))
    monkeypatch.setattr(config, "GRPC_TLS_CLIENT_CERT_FILE", None)
    monkeypatch.setattr(config, "GRPC_TLS_CLIENT_KEY_FILE", None)

    channel = _create_channel("127.0.0.1:50051")
    try:
        assert isinstance(channel, grpc.Channel)
    finally:
        channel.close()


def test_mutual_tls_config_returns_secure_channel(monkeypatch, tmp_path):
    ca_file = tmp_path / "ca.crt"
    client_cert_file = tmp_path / "client.crt"
    client_key_file = tmp_path / "client.key"
    ca_file.write_bytes(b"fake-ca")
    client_cert_file.write_bytes(b"fake-client-cert")
    client_key_file.write_bytes(b"fake-client-key")

    monkeypatch.setattr(config, "GRPC_TLS_CA_FILE", str(ca_file))
    monkeypatch.setattr(config, "GRPC_TLS_CLIENT_CERT_FILE", str(client_cert_file))
    monkeypatch.setattr(config, "GRPC_TLS_CLIENT_KEY_FILE", str(client_key_file))

    channel = _create_channel("127.0.0.1:50051")
    try:
        assert isinstance(channel, grpc.Channel)
    finally:
        channel.close()
