from functools import lru_cache
from pathlib import Path

import grpc

import auth_service_pb2
import auth_service_pb2_grpc
import cash_service_pb2
import cash_service_pb2_grpc
import client_service_pb2
import client_service_pb2_grpc
import dashboard_service_pb2
import dashboard_service_pb2_grpc
import loan_service_pb2
import loan_service_pb2_grpc
from cas_client import config


class AuthError(Exception):
    def __init__(self, code: grpc.StatusCode, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ApiError(Exception):
    """Raised by ClientServiceClient/LoanServiceClient -- same shape as AuthError,
    named generically since it covers more than authentication failures."""

    def __init__(self, code: grpc.StatusCode, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _bearer(access_token: str) -> tuple:
    return (("authorization", f"Bearer {access_token}"),)


def _channel_options() -> list[tuple[str, int]]:
    """Keepalive del lado cliente -- la mitad que le corresponde al par de
    ajustes descrito en cas_client/config.py. `permit_without_calls` es la
    opción clave: el cliente pasa la mayor parte de la jornada sin RPC en
    vuelo, y es exactamente ahí cuando el router descarta la conexión."""
    return [
        ("grpc.keepalive_time_ms", config.GRPC_KEEPALIVE_TIME_MS),
        ("grpc.keepalive_timeout_ms", config.GRPC_KEEPALIVE_TIMEOUT_MS),
        ("grpc.keepalive_permit_without_calls", 1),
        ("grpc.http2.max_pings_without_data", 0),
        ("grpc.http2.min_time_between_pings_ms", config.GRPC_KEEPALIVE_TIME_MS),
    ]


def _invoke(rpc, request, *, error_cls, access_token: str | None = None, timeout=None):
    """Punto único por el que pasa toda RPC de este módulo.

    Existe para que la política de conexión se defina una sola vez en vez de
    repetirse en la treintena de métodos de abajo (que además ya repetían el
    mismo try/except palabra por palabra):

    * **timeout**: sin un plazo, una llamada contra un servidor inalcanzable
      se cuelga indefinidamente y deja al AsyncWorker esperando para siempre,
      con la barra de progreso girando y sin forma de cancelar.
    * **wait_for_ready**: cuando el keepalive detecta que la conexión murió,
      el canal se reconecta solo en segundo plano. Sin esta bandera, la
      llamada que caiga justo en esa ventana falla con UNAVAILABLE aunque la
      reconexión esté por completarse un instante después; con ella, espera
      (hasta el timeout) y sale bien. No reintenta nada ya enviado, así que es
      igual de seguro para un RecordPayment que para una consulta.
    """
    try:
        return rpc(
            request,
            metadata=_bearer(access_token) if access_token is not None else None,
            timeout=timeout or config.GRPC_CALL_TIMEOUT_SECONDS,
            wait_for_ready=True,
        )
    except grpc.RpcError as exc:
        raise error_cls(exc.code(), exc.details()) from exc


@lru_cache(maxsize=1)
def _ssl_channel_credentials() -> grpc.ChannelCredentials:
    """Builds the TLS ChannelCredentials once and reuses it for every
    *ServiceClient -- main_window.py constructs four of these (Auth, Client,
    Loan, Dashboard) at startup, and grpc.ChannelCredentials is immutable /
    safe to share, so re-reading the same cert/key files from disk for each
    one is pure waste. Cached at module scope rather than per-client since
    the underlying files (config.GRPC_TLS_*) don't change during the
    process's lifetime."""
    root_certificates = Path(config.GRPC_TLS_CA_FILE).read_bytes()
    client_certificate_chain = None
    client_private_key = None
    if config.GRPC_TLS_CLIENT_CERT_FILE and config.GRPC_TLS_CLIENT_KEY_FILE:
        client_certificate_chain = Path(config.GRPC_TLS_CLIENT_CERT_FILE).read_bytes()
        client_private_key = Path(config.GRPC_TLS_CLIENT_KEY_FILE).read_bytes()

    return grpc.ssl_channel_credentials(
        root_certificates=root_certificates,
        private_key=client_private_key,
        certificate_chain=client_certificate_chain,
    )


def _create_channel(target: str) -> grpc.Channel:
    """Secure channel when GRPC_TLS_CA_FILE is configured (trusting that CA
    -- or the server's own cert, if self-signed -- to verify the server's
    identity), insecure otherwise. Mirrors cas_server/server.py's own opt-in
    TLS; every *ServiceClient below goes through this instead of calling
    grpc.insecure_channel directly, so all five stay in sync automatically --
    including the keepalive options, which only work if every channel gets
    them. See cas_client/.env.example for the GRPC_TLS_* variables."""
    options = _channel_options()
    if not config.GRPC_TLS_CA_FILE:
        return grpc.insecure_channel(target, options=options)

    return grpc.secure_channel(target, _ssl_channel_credentials(), options=options)


class AuthClient:
    """Thin wrapper around the AuthService gRPC stub.

    Per ES-000 §3: the client never talks to the database directly --
    every operation goes through a stub like this one.
    """

    def __init__(self, host: str | None = None, port: int | None = None):
        target = f"{host or config.GRPC_SERVER_HOST}:{port or config.GRPC_PORT}"
        self._channel = _create_channel(target)
        self._stub = auth_service_pb2_grpc.AuthServiceStub(self._channel)

    @property
    def channel(self) -> grpc.Channel:
        """Expuesto para `connection_status.ConnectionMonitor`, que observa la
        conectividad del canal para el indicador de la barra superior. Es el
        canal de Auth y no otro porque es el único que existe antes del login;
        los cinco apuntan al mismo host, así que da igual cuál se observe."""
        return self._channel

    def login(self, username: str, password: str) -> auth_service_pb2.LoginResponse:
        return _invoke(
            self._stub.Login,
            auth_service_pb2.LoginRequest(username=username, password=password),
            error_cls=AuthError,
        )

    def logout(self, access_token: str) -> bool:
        return _invoke(
            self._stub.Logout,
            auth_service_pb2.LogoutRequest(access_token=access_token),
            access_token=access_token,
            error_cls=AuthError,
        ).success

    def create_user(
        self, access_token: str, **fields
    ) -> auth_service_pb2.CreateUserResponse:
        return _invoke(
            self._stub.CreateUser,
            auth_service_pb2.CreateUserRequest(**fields),
            access_token=access_token,
            error_cls=AuthError,
        )

    def list_users(self, access_token: str) -> auth_service_pb2.ListUsersResponse:
        return _invoke(
            self._stub.ListUsers,
            auth_service_pb2.ListUsersRequest(),
            access_token=access_token,
            error_cls=AuthError,
        )

    def reset_password(
        self, access_token: str, **fields
    ) -> auth_service_pb2.ResetPasswordResponse:
        return _invoke(
            self._stub.ResetPassword,
            auth_service_pb2.ResetPasswordRequest(**fields),
            access_token=access_token,
            error_cls=AuthError,
        )


class ClientServiceClient:
    """Thin wrapper around the ClientService gRPC stub. Every RPC here requires
    a Bearer token (none of them are in rbac.PUBLIC_METHODS)."""

    def __init__(self, host: str | None = None, port: int | None = None):
        target = f"{host or config.GRPC_SERVER_HOST}:{port or config.GRPC_PORT}"
        self._channel = _create_channel(target)
        self._stub = client_service_pb2_grpc.ClientServiceStub(self._channel)

    def create_client(
        self, access_token: str, **fields
    ) -> client_service_pb2.CreateClientResponse:
        return _invoke(
            self._stub.CreateClient,
            client_service_pb2.CreateClientRequest(**fields),
            access_token=access_token,
            error_cls=ApiError,
        )

    def get_client_by_id(
        self, access_token: str, client_id: str
    ) -> client_service_pb2.GetClientByIdResponse:
        return _invoke(
            self._stub.GetClientById,
            client_service_pb2.GetClientByIdRequest(client_id=client_id),
            access_token=access_token,
            error_cls=ApiError,
        )

    def search_clients(
        self,
        access_token: str,
        search_term: str = "",
        page_size: int = 0,
        page_token: int = 0,
    ) -> client_service_pb2.SearchClientsResponse:
        return _invoke(
            self._stub.SearchClients,
            client_service_pb2.SearchClientsRequest(
                search_term=search_term, page_size=page_size, page_token=page_token
            ),
            access_token=access_token,
            error_cls=ApiError,
        )

    def update_client(
        self, access_token: str, **fields
    ) -> client_service_pb2.UpdateClientResponse:
        return _invoke(
            self._stub.UpdateClient,
            client_service_pb2.UpdateClientRequest(**fields),
            access_token=access_token,
            error_cls=ApiError,
        )

    def deactivate_client(
        self, access_token: str, client_id: str
    ) -> client_service_pb2.DeactivateClientResponse:
        return _invoke(
            self._stub.DeactivateClient,
            client_service_pb2.DeactivateClientRequest(client_id=client_id),
            access_token=access_token,
            error_cls=ApiError,
        )

    def update_national_id(
        self, access_token: str, client_id: str, new_national_id: str
    ) -> client_service_pb2.UpdateNationalIdResponse:
        return _invoke(
            self._stub.UpdateNationalId,
            client_service_pb2.UpdateNationalIdRequest(
                client_id=client_id, new_national_id=new_national_id
            ),
            access_token=access_token,
            error_cls=ApiError,
        )


class LoanServiceClient:
    """Thin wrapper around the LoanService gRPC stub. Every RPC here requires
    a Bearer token (none of them are in rbac.PUBLIC_METHODS)."""

    def __init__(self, host: str | None = None, port: int | None = None):
        target = f"{host or config.GRPC_SERVER_HOST}:{port or config.GRPC_PORT}"
        self._channel = _create_channel(target)
        self._stub = loan_service_pb2_grpc.LoanServiceStub(self._channel)

    def create_loan(
        self, access_token: str, **fields
    ) -> loan_service_pb2.CreateLoanResponse:
        return _invoke(
            self._stub.CreateLoan,
            loan_service_pb2.CreateLoanRequest(**fields),
            access_token=access_token,
            error_cls=ApiError,
        )

    def update_loan_proposal(
        self, access_token: str, **fields
    ) -> loan_service_pb2.UpdateLoanProposalResponse:
        return _invoke(
            self._stub.UpdateLoanProposal,
            loan_service_pb2.UpdateLoanProposalRequest(**fields),
            access_token=access_token,
            error_cls=ApiError,
        )

    def update_loan_guarantee(
        self, access_token: str, **fields
    ) -> loan_service_pb2.UpdateLoanGuaranteeResponse:
        return _invoke(
            self._stub.UpdateLoanGuarantee,
            loan_service_pb2.UpdateLoanGuaranteeRequest(**fields),
            access_token=access_token,
            error_cls=ApiError,
        )

    def update_loan_charges(
        self, access_token: str, **fields
    ) -> loan_service_pb2.UpdateLoanChargesResponse:
        return _invoke(
            self._stub.UpdateLoanCharges,
            loan_service_pb2.UpdateLoanChargesRequest(**fields),
            access_token=access_token,
            error_cls=ApiError,
        )

    def update_installment_amount(
        self, access_token: str, **fields
    ) -> loan_service_pb2.UpdateInstallmentAmountResponse:
        return _invoke(
            self._stub.UpdateInstallmentAmount,
            loan_service_pb2.UpdateInstallmentAmountRequest(**fields),
            access_token=access_token,
            error_cls=ApiError,
        )

    def get_loan_by_id(
        self, access_token: str, loan_id: str
    ) -> loan_service_pb2.GetLoanByIdResponse:
        return _invoke(
            self._stub.GetLoanById,
            loan_service_pb2.GetLoanByIdRequest(loan_id=loan_id),
            access_token=access_token,
            error_cls=ApiError,
        )

    def list_client_loans(
        self, access_token: str, client_id: str
    ) -> loan_service_pb2.ListClientLoansResponse:
        return _invoke(
            self._stub.ListClientLoans,
            loan_service_pb2.ListClientLoansRequest(client_id=client_id),
            access_token=access_token,
            error_cls=ApiError,
        )

    def list_active_loans(
        self, access_token: str
    ) -> loan_service_pb2.ListActiveLoansResponse:
        return _invoke(
            self._stub.ListActiveLoans,
            loan_service_pb2.ListActiveLoansRequest(),
            access_token=access_token,
            error_cls=ApiError,
        )

    def approve_loan(
        self, access_token: str, loan_id: str
    ) -> loan_service_pb2.ApproveLoanResponse:
        return _invoke(
            self._stub.ApproveLoan,
            loan_service_pb2.ApproveLoanRequest(loan_id=loan_id),
            access_token=access_token,
            error_cls=ApiError,
        )

    def disburse_loan(
        self, access_token: str, loan_id: str
    ) -> loan_service_pb2.DisburseLoanResponse:
        return _invoke(
            self._stub.DisburseLoan,
            loan_service_pb2.DisburseLoanRequest(loan_id=loan_id),
            access_token=access_token,
            error_cls=ApiError,
        )

    def record_payment(
        self,
        access_token: str,
        loan_id: str,
        transfer_reference: str,
        *,
        installment_number: int = 0,
        amount: str = "",
        payment_method: str = "",
    ) -> loan_service_pb2.RecordPaymentResponse:
        """`installment_number` (BR-LOAN-010) is the normal path from the UI
        now -- the server recalculates and enforces the fixed amount owed
        for that specific installment, ignoring `amount`. The free-form
        `amount` (installment_number=0) path stays for any future
        non-installment-specific payment, but the current UI always
        supplies installment_number.

        `payment_method` (BR-CAJA-004) is "EFECTIVO" or "TRANSFERENCIA"; an
        empty string means TRANSFERENCIA server-side. EFECTIVO needs an open
        cash session for the logged-in user and ignores transfer_reference.
        """
        return _invoke(
            self._stub.RecordPayment,
            loan_service_pb2.RecordPaymentRequest(
                loan_id=loan_id,
                amount=amount,
                transfer_reference=transfer_reference,
                installment_number=installment_number,
                payment_method=payment_method,
            ),
            access_token=access_token,
            error_cls=ApiError,
        )

    def mark_defaulted(
        self, access_token: str, loan_id: str
    ) -> loan_service_pb2.MarkDefaultedResponse:
        return _invoke(
            self._stub.MarkDefaulted,
            loan_service_pb2.MarkDefaultedRequest(loan_id=loan_id),
            access_token=access_token,
            error_cls=ApiError,
        )

    def get_amortization_schedule(
        self, access_token: str, loan_id: str
    ) -> loan_service_pb2.GetAmortizationScheduleResponse:
        return _invoke(
            self._stub.GetAmortizationSchedule,
            loan_service_pb2.GetAmortizationScheduleRequest(loan_id=loan_id),
            access_token=access_token,
            error_cls=ApiError,
        )

    def delete_loan(
        self, access_token: str, loan_id: str, reason: str
    ) -> loan_service_pb2.DeleteLoanResponse:
        """BR-LOAN-012: borra un préstamo cargado por error. `reason` es
        obligatorio -- el servidor rechaza un motivo vacío, porque la fila
        desaparece y el registro de auditoría es lo único que queda."""
        return _invoke(
            self._stub.DeleteLoan,
            loan_service_pb2.DeleteLoanRequest(loan_id=loan_id, reason=reason),
            access_token=access_token,
            error_cls=ApiError,
        )


class DashboardServiceClient:
    """Thin wrapper around the DashboardService gRPC stub -- feeds the home
    screen's stat tiles. Requires a Bearer token like Client/LoanServiceClient."""

    def __init__(self, host: str | None = None, port: int | None = None):
        target = f"{host or config.GRPC_SERVER_HOST}:{port or config.GRPC_PORT}"
        self._channel = _create_channel(target)
        self._stub = dashboard_service_pb2_grpc.DashboardServiceStub(self._channel)

    def get_dashboard_stats(
        self, access_token: str
    ) -> dashboard_service_pb2.GetDashboardStatsResponse:
        return _invoke(
            self._stub.GetDashboardStats,
            dashboard_service_pb2.GetDashboardStatsRequest(),
            access_token=access_token,
            error_cls=ApiError,
        )

    def get_period_report(
        self, access_token: str, start_date: str, end_date: str
    ) -> dashboard_service_pb2.GetPeriodReportResponse:
        """BR-DASH-002. `start_date`/`end_date` van en formato de cable
        (YYYY-MM-DD) -- la vista muestra DD/MM/AAAA y traduce con
        formatting.fecha_a_iso() antes de llamar acá."""
        return _invoke(
            self._stub.GetPeriodReport,
            dashboard_service_pb2.GetPeriodReportRequest(
                start_date=start_date, end_date=end_date
            ),
            access_token=access_token,
            error_cls=ApiError,
        )


class CashServiceClient:
    """Thin wrapper around the CashService gRPC stub (BR-CAJA-*).

    None of these take a cashier id: the server resolves the cash session
    from the bearer token, so the client can't accidentally address someone
    else's caja. `close_cash_session`'s optional `session_id` is the one
    exception, for a manager closing a session a cashier left open.
    """

    def __init__(self, host: str | None = None, port: int | None = None):
        target = f"{host or config.GRPC_SERVER_HOST}:{port or config.GRPC_PORT}"
        self._channel = _create_channel(target)
        self._stub = cash_service_pb2_grpc.CashServiceStub(self._channel)

    def open_cash_session(
        self, access_token: str, opening_amount: str, notes: str = ""
    ) -> cash_service_pb2.CashSessionDetail:
        return _invoke(
            self._stub.OpenCashSession,
            cash_service_pb2.OpenCashSessionRequest(
                opening_amount=opening_amount, notes=notes
            ),
            access_token=access_token,
            error_cls=ApiError,
        )

    def get_current_cash_session(
        self, access_token: str
    ) -> cash_service_pb2.GetCurrentCashSessionResponse:
        return _invoke(
            self._stub.GetCurrentCashSession,
            cash_service_pb2.GetCurrentCashSessionRequest(),
            access_token=access_token,
            error_cls=ApiError,
        )

    def register_cash_movement(
        self, access_token: str, movement_type: str, amount: str, concept: str
    ) -> cash_service_pb2.CashSessionDetail:
        return _invoke(
            self._stub.RegisterCashMovement,
            cash_service_pb2.RegisterCashMovementRequest(
                movement_type=movement_type, amount=amount, concept=concept
            ),
            access_token=access_token,
            error_cls=ApiError,
        )

    def close_cash_session(
        self,
        access_token: str,
        counted_amount: str,
        notes: str = "",
        session_id: str = "",
    ) -> cash_service_pb2.CashSessionDetail:
        return _invoke(
            self._stub.CloseCashSession,
            cash_service_pb2.CloseCashSessionRequest(
                counted_amount=counted_amount, notes=notes, session_id=session_id
            ),
            access_token=access_token,
            error_cls=ApiError,
        )

    def list_cash_sessions(
        self, access_token: str, start_date: str = "", end_date: str = ""
    ) -> cash_service_pb2.ListCashSessionsResponse:
        """Dates go in wire format (YYYY-MM-DD) -- the view shows DD/MM/AAAA
        and converts with formatting.fecha_a_iso() before calling here, same
        contract as DashboardServiceClient.get_period_report."""
        return _invoke(
            self._stub.ListCashSessions,
            cash_service_pb2.ListCashSessionsRequest(
                start_date=start_date, end_date=end_date
            ),
            access_token=access_token,
            error_cls=ApiError,
        )
