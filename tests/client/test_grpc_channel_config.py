"""Cubre las comprobaciones de configuración de _create_channel().

Existen por un modo de falla real del despliegue LAN: una PC cliente con
GRPC_TLS_CA_FILE vacío o mal apuntado abría un canal *inseguro* sin avisar, el
servidor (que exige TLS) rechazaba el handshake -- "SSL_ERROR_SSL:
WRONG_VERSION_NUMBER" en logs/server.log -- y el operador solo veía el genérico
"No se pudo conectar con el servidor", igual que si el servidor estuviera
apagado. Estas pruebas fijan que ahora falla temprano y con un mensaje que
nombra la variable o el archivo a corregir.
"""

import grpc
import pytest

from cas_client import config, grpc_client
from cas_client.grpc_client import ConfigurationError, _create_channel


@pytest.fixture(autouse=True)
def _tls_sin_disco(monkeypatch):
    """Evita leer certificados reales: lo que se prueba acá es la validación
    previa, no la construcción de credenciales de gRPC."""
    monkeypatch.setattr(
        grpc_client, "_ssl_channel_credentials", lambda: grpc.ssl_channel_credentials()
    )


def test_sin_certificado_contra_host_remoto_falla_con_mensaje_util(monkeypatch):
    monkeypatch.setattr(config, "GRPC_TLS_CA_FILE", None)
    monkeypatch.setattr(config, "GRPC_ALLOW_INSECURE", False)

    with pytest.raises(ConfigurationError) as error:
        _create_channel("DESKTOP-5H7BABS:50051")

    assert "GRPC_TLS_CA_FILE" in str(error.value)


def test_sin_certificado_contra_localhost_sigue_permitiendo_texto_plano(monkeypatch):
    """El desarrollo local contra un servidor sin TLS no se rompe."""
    monkeypatch.setattr(config, "GRPC_TLS_CA_FILE", None)
    monkeypatch.setattr(config, "GRPC_ALLOW_INSECURE", False)

    canal = _create_channel("127.0.0.1:50051")

    assert canal is not None
    canal.close()


def test_escape_hatch_permite_texto_plano_contra_host_remoto(monkeypatch):
    monkeypatch.setattr(config, "GRPC_TLS_CA_FILE", None)
    monkeypatch.setattr(config, "GRPC_ALLOW_INSECURE", True)

    canal = _create_channel("DESKTOP-5H7BABS:50051")

    assert canal is not None
    canal.close()


def test_certificado_inexistente_falla_nombrando_la_ruta(monkeypatch, tmp_path):
    faltante = tmp_path / "no_esta" / "server.crt"
    monkeypatch.setattr(config, "GRPC_TLS_CA_FILE", str(faltante))

    with pytest.raises(ConfigurationError) as error:
        _create_channel("DESKTOP-5H7BABS:50051")

    assert str(faltante) in str(error.value)


def test_certificado_existente_construye_el_canal(monkeypatch, tmp_path):
    certificado = tmp_path / "server.crt"
    certificado.write_text("-----BEGIN CERTIFICATE-----\n")
    monkeypatch.setattr(config, "GRPC_TLS_CA_FILE", str(certificado))
    monkeypatch.setattr(config, "GRPC_TLS_CLIENT_CERT_FILE", None)
    monkeypatch.setattr(config, "GRPC_TLS_CLIENT_KEY_FILE", None)

    canal = _create_channel("DESKTOP-5H7BABS:50051")

    assert canal is not None
    canal.close()


def test_certificado_de_cliente_inexistente_tambien_falla(monkeypatch, tmp_path):
    certificado = tmp_path / "server.crt"
    certificado.write_text("-----BEGIN CERTIFICATE-----\n")
    monkeypatch.setattr(config, "GRPC_TLS_CA_FILE", str(certificado))
    monkeypatch.setattr(
        config, "GRPC_TLS_CLIENT_CERT_FILE", str(tmp_path / "cliente.crt")
    )
    monkeypatch.setattr(config, "GRPC_TLS_CLIENT_KEY_FILE", None)

    with pytest.raises(ConfigurationError) as error:
        _create_channel("DESKTOP-5H7BABS:50051")

    assert "GRPC_TLS_CLIENT_CERT_FILE" in str(error.value)
