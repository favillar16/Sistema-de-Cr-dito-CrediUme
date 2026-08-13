import logging
from concurrent import futures
from pathlib import Path

import grpc

import auth_service_pb2_grpc
import client_service_pb2_grpc
import dashboard_service_pb2_grpc
import loan_service_pb2_grpc
from cas_server import config
from cas_server.security.interceptor import AuthInterceptor
from cas_server.services.auth_service import AuthServicer
from cas_server.services.client_service import ClientServicer
from cas_server.services.dashboard_service import DashboardServicer
from cas_server.services.loan_service import LoanServicer

# NOTE: ES-001/ES-004 call for TLS on the gRPC channel in the real LAN
# deployment. TLS is now supported (see _build_server_credentials below) but
# stays opt-in: set GRPC_TLS_CERT_FILE/GRPC_TLS_KEY_FILE in cas_server/.env
# to enable it. Left unset, the server still binds an insecure port, same as
# before -- this keeps local dev working without requiring certificates, and
# leaves the actual cert-issuance strategy (self-signed vs CA-issued,
# rotation) as the deployment/ops decision ES-004 always said it was; this
# session only added the code path to use certificates once you have them.


def _build_server_credentials() -> grpc.ServerCredentials | None:
    """None when TLS isn't configured (GRPC_TLS_CERT_FILE/GRPC_TLS_KEY_FILE
    unset) -- the caller falls back to add_insecure_port in that case."""
    if not (config.GRPC_TLS_CERT_FILE and config.GRPC_TLS_KEY_FILE):
        return None

    private_key = Path(config.GRPC_TLS_KEY_FILE).read_bytes()
    certificate_chain = Path(config.GRPC_TLS_CERT_FILE).read_bytes()

    root_certificates = None
    require_client_auth = False
    if config.GRPC_TLS_CLIENT_CA_FILE:
        root_certificates = Path(config.GRPC_TLS_CLIENT_CA_FILE).read_bytes()
        require_client_auth = True

    return grpc.ssl_server_credentials(
        [(private_key, certificate_chain)],
        root_certificates=root_certificates,
        require_client_auth=require_client_auth,
    )


def serve() -> None:
    logging.basicConfig(level=logging.INFO)
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=10),
        interceptors=[AuthInterceptor()],
    )
    auth_service_pb2_grpc.add_AuthServiceServicer_to_server(AuthServicer(), server)
    client_service_pb2_grpc.add_ClientServiceServicer_to_server(
        ClientServicer(), server
    )
    loan_service_pb2_grpc.add_LoanServiceServicer_to_server(LoanServicer(), server)
    dashboard_service_pb2_grpc.add_DashboardServiceServicer_to_server(
        DashboardServicer(), server
    )

    address = f"{config.GRPC_HOST}:{config.GRPC_PORT}"
    logger = logging.getLogger(__name__)
    credentials = _build_server_credentials()
    if credentials is not None:
        server.add_secure_port(address, credentials)
        logger.info("cas_server listening on %s (TLS enabled)", address)
    else:
        server.add_insecure_port(address)
        logger.warning(
            "cas_server listening on %s WITHOUT TLS -- set GRPC_TLS_CERT_FILE/"
            "GRPC_TLS_KEY_FILE in cas_server/.env before using this outside "
            "local development (ES-004)",
            address,
        )
    server.start()
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
