"""Diagnóstico de conexión para una PC cliente (ES-004, despliegue LAN).

Se corre EN LA PC CLIENTE, desde la raíz del repo y con el venv activado:

    python scripts/diagnostico_cliente.py

Revisa, en el mismo orden en que fallan las cosas en la práctica:

    1. la configuración de cas_client/.env (host, puerto, certificado),
    2. la resolución del nombre del servidor,
    3. el puerto TCP 50051,
    4. el handshake TLS,
    5. un RPC real (Login con credenciales inexistentes), que es lo único que
       prueba que además del socket están vivos el interceptor, el servicer y
       Postgres.

Cada paso imprime OK/FALLA y, cuando falla, qué corregir. No necesita
credenciales válidas ni toca la base de datos: el Login de prueba usa un
usuario que no existe y se espera que el servidor conteste UNAUTHENTICATED.

Solo depende de cas_client (no importa cas_server), así que corre en una PC que
únicamente tiene el cliente instalado -- a diferencia de `pytest`, que muere al
recolectar porque tests/conftest.py exige las variables POSTGRES_*.
"""

import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import grpc  # noqa: E402

# cas_client/__init__.py es el que agrega su propio directorio a sys.path, que
# es lo que hace resolubles los imports planos de los stubs generados (ver
# CLAUDE.md, "Generated stub imports"): tiene que ir antes que auth_service_pb2.
from cas_client import config  # noqa: E402
from cas_client.grpc_client import ConfigurationError, _create_channel  # noqa: E402

import auth_service_pb2  # noqa: E402
import auth_service_pb2_grpc  # noqa: E402

TIEMPO_LIMITE_SEGUNDOS = 8


def _titulo(texto: str) -> None:
    print(f"\n{texto}\n{'-' * len(texto)}")


def _ok(texto: str) -> None:
    print(f"  [OK]    {texto}")


def _falla(texto: str) -> None:
    print(f"  [FALLA] {texto}")


def _nota(texto: str) -> None:
    print(f"          {texto}")


def revisar_configuracion() -> bool:
    _titulo("1. Configuración (cas_client/.env)")
    archivo_env = ROOT / "cas_client" / ".env"
    if archivo_env.is_file():
        _ok(f"existe {archivo_env}")
    else:
        _falla(f"no existe {archivo_env}")
        _nota("copiar cas_client/.env.example a cas_client/.env y completarlo.")
        return False

    _ok(f"GRPC_SERVER_HOST = {config.GRPC_SERVER_HOST}")
    _ok(f"GRPC_PORT        = {config.GRPC_PORT}")

    if not config.GRPC_TLS_CA_FILE:
        _falla("GRPC_TLS_CA_FILE no está definido")
        _nota("el servidor de esta instalación exige TLS: sin esta variable el")
        _nota("cliente ni siquiera intenta conectarse (ver _create_channel).")
        _nota("Copiar certs\\server.crt de la PC servidor y apuntar la variable")
        _nota("a esa copia (nunca copiar server.key).")
        return False

    certificado = Path(config.GRPC_TLS_CA_FILE)
    if certificado.is_file():
        _ok(f"GRPC_TLS_CA_FILE = {certificado} ({certificado.stat().st_size} bytes)")
        return True

    _falla(f"GRPC_TLS_CA_FILE apunta a un archivo inexistente: {certificado}")
    _nota("corregir la ruta en cas_client/.env o copiar el certificado ahí.")
    return False


def revisar_dns() -> list[str]:
    _titulo("2. Resolución del nombre del servidor")
    host = config.GRPC_SERVER_HOST
    try:
        infos = socket.getaddrinfo(host, config.GRPC_PORT, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        _falla(f"no se pudo resolver «{host}»: {exc}")
        _nota("verificar que la PC servidor esté encendida y en la misma red.")
        _nota("Como respaldo se puede usar la IP RESERVADA del servidor, pero")
        _nota("solo funciona si esa IP figura en el SAN del certificado (ver la")
        _nota("sección «Reserva DHCP» de docs/CONFIGURACION_CLIENTE_LAN.md).")
        return []

    direcciones = []
    for familia, _, _, _, sockaddr in infos:
        direccion = sockaddr[0]
        if direccion not in direcciones:
            direcciones.append(direccion)
            _ok(
                f"{host} -> {direccion} ({'IPv6' if familia == socket.AF_INET6 else 'IPv4'})"
            )
    return direcciones


def revisar_tcp(direcciones: list[str]) -> bool:
    _titulo(f"3. Puerto TCP {config.GRPC_PORT}")
    if not direcciones:
        _falla("sin direcciones para probar (falló el paso anterior)")
        return False

    alguna_ok = False
    for direccion in direcciones:
        familia = socket.AF_INET6 if ":" in direccion else socket.AF_INET
        sock = socket.socket(familia, socket.SOCK_STREAM)
        sock.settimeout(4)
        try:
            sock.connect((direccion, config.GRPC_PORT))
            _ok(f"conexión establecida con {direccion}:{config.GRPC_PORT}")
            alguna_ok = True
        except OSError as exc:
            _falla(f"{direccion}:{config.GRPC_PORT} -> {exc.strerror or exc}")
        finally:
            sock.close()

    if not alguna_ok:
        _nota("el servidor no está escuchando, o el Firewall de Windows de la PC")
        _nota("servidor no tiene la regla de entrada «CAS Server gRPC 50051».")
        _nota("Nota: «ping» al servidor puede fallar aunque esto funcione -- el")
        _nota("firewall bloquea ICMP; lo que importa es esta prueba TCP.")
    return alguna_ok


def revisar_tls_y_rpc() -> bool:
    objetivo = f"{config.GRPC_SERVER_HOST}:{config.GRPC_PORT}"

    _titulo("4. Handshake TLS")
    try:
        canal = _create_channel(objetivo)
    except ConfigurationError as exc:
        _falla(str(exc))
        return False

    try:
        grpc.channel_ready_future(canal).result(timeout=TIEMPO_LIMITE_SEGUNDOS)
        _ok(f"canal listo contra {objetivo}")
    except grpc.FutureTimeoutError:
        _falla(f"el canal no quedó listo en {TIEMPO_LIMITE_SEGUNDOS}s")
        _nota("causas típicas, en orden de frecuencia:")
        _nota("  a) el certificado copiado no es el que usa hoy el servidor;")
        _nota("  b) GRPC_SERVER_HOST usa una IP que NO figura en el SAN del")
        _nota("     certificado -- usar el nombre de red del servidor;")
        _nota("  c) el servidor está caído (revisar logs\\server.log allá).")
        return False

    _titulo("5. RPC real (Login con un usuario inexistente)")
    stub = auth_service_pb2_grpc.AuthServiceStub(canal)
    try:
        stub.Login(
            auth_service_pb2.LoginRequest(
                username="__diagnostico__", password="__diagnostico__"
            ),
            timeout=TIEMPO_LIMITE_SEGUNDOS,
        )
        _falla("el servidor aceptó credenciales de prueba (no debería)")
        return False
    except grpc.RpcError as exc:
        if exc.code() == grpc.StatusCode.UNAUTHENTICATED:
            _ok(f"el servidor respondió UNAUTHENTICATED: {exc.details()}")
            _nota("esto prueba que responden el interceptor, el servicio y la")
            _nota("base de datos -- no solo que el socket abre.")
            return True
        _falla(f"{exc.code().name}: {exc.details()}")
        if exc.code() == grpc.StatusCode.UNAVAILABLE:
            _nota("mismas causas que el punto 4.")
        return False


def main() -> int:
    print(f"Diagnóstico de conexión del cliente -- repo: {ROOT}")
    configuracion_ok = revisar_configuracion()
    direcciones = revisar_dns()
    tcp_ok = revisar_tcp(direcciones)

    if not configuracion_ok:
        _titulo("Resultado")
        print("  Corregir primero la configuración (punto 1) y volver a correr.")
        return 1

    rpc_ok = revisar_tls_y_rpc() if tcp_ok else False

    _titulo("Resultado")
    if rpc_ok:
        print("  Todo OK: esta PC puede registrar clientes contra el servidor.")
        print("  Si la app igual falla, anotar el mensaje exacto que muestra.")
        return 0
    print("  Hay al menos un punto en FALLA -- ver las notas de arriba.")
    print("  Guía completa: docs/CONFIGURACION_CLIENTE_LAN.md")
    return 1


if __name__ == "__main__":
    sys.exit(main())
