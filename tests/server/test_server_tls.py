"""Coverage for cas_server/server.py's opt-in TLS support
(_build_server_credentials) -- added alongside GRPC_TLS_* in config.py.

Doesn't exercise a real TLS handshake (would need real certificates, and
`cryptography` isn't a project dependency) -- just the branching logic that
decides insecure vs. secure, since that's what a missing/partial config
would actually get wrong in practice. grpc.ssl_server_credentials() itself
doesn't validate PEM content at construction time (only once actually bound
to a port), so dummy byte content is enough here.
"""

import grpc

from cas_server import config
from cas_server.server import _build_server_credentials


def test_no_tls_config_returns_none(monkeypatch):
    monkeypatch.setattr(config, "GRPC_TLS_CERT_FILE", None)
    monkeypatch.setattr(config, "GRPC_TLS_KEY_FILE", None)
    monkeypatch.setattr(config, "GRPC_TLS_CLIENT_CA_FILE", None)

    assert _build_server_credentials() is None


def test_partial_tls_config_returns_none(monkeypatch, tmp_path):
    """Only one of the two required files set -- must not half-enable TLS
    or crash trying to read a file that isn't there."""
    cert_file = tmp_path / "server.crt"
    cert_file.write_bytes(b"fake-cert")

    monkeypatch.setattr(config, "GRPC_TLS_CERT_FILE", str(cert_file))
    monkeypatch.setattr(config, "GRPC_TLS_KEY_FILE", None)
    monkeypatch.setattr(config, "GRPC_TLS_CLIENT_CA_FILE", None)

    assert _build_server_credentials() is None


def test_full_tls_config_returns_server_credentials(monkeypatch, tmp_path):
    cert_file = tmp_path / "server.crt"
    key_file = tmp_path / "server.key"
    cert_file.write_bytes(b"fake-cert")
    key_file.write_bytes(b"fake-key")

    monkeypatch.setattr(config, "GRPC_TLS_CERT_FILE", str(cert_file))
    monkeypatch.setattr(config, "GRPC_TLS_KEY_FILE", str(key_file))
    monkeypatch.setattr(config, "GRPC_TLS_CLIENT_CA_FILE", None)

    credentials = _build_server_credentials()
    assert isinstance(credentials, grpc.ServerCredentials)


def test_tls_config_with_client_ca_enables_mutual_tls(monkeypatch, tmp_path):
    """GRPC_TLS_CLIENT_CA_FILE set -> mutual TLS (require_client_auth=True).
    grpc.ServerCredentials doesn't expose that flag back for inspection, so
    this only asserts construction succeeds with all three files -- the
    require_client_auth branch itself is exercised by line coverage."""
    cert_file = tmp_path / "server.crt"
    key_file = tmp_path / "server.key"
    ca_file = tmp_path / "ca.crt"
    cert_file.write_bytes(b"fake-cert")
    key_file.write_bytes(b"fake-key")
    ca_file.write_bytes(b"fake-ca")

    monkeypatch.setattr(config, "GRPC_TLS_CERT_FILE", str(cert_file))
    monkeypatch.setattr(config, "GRPC_TLS_KEY_FILE", str(key_file))
    monkeypatch.setattr(config, "GRPC_TLS_CLIENT_CA_FILE", str(ca_file))

    credentials = _build_server_credentials()
    assert isinstance(credentials, grpc.ServerCredentials)
