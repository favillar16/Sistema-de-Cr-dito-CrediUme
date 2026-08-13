import logging
from concurrent import futures

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
# deployment. That's out of scope for this phase (auth vertical slice) --
# this binds an insecure port for local development only.


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
    server.add_insecure_port(address)
    server.start()
    logging.getLogger(__name__).info("cas_server listening on %s", address)
    server.wait_for_termination()


if __name__ == "__main__":
    serve()
