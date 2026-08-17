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
    grpc.insecure_channel directly, so all four stay in sync automatically.
    See cas_client/.env.example for the GRPC_TLS_* variables."""
    if not config.GRPC_TLS_CA_FILE:
        return grpc.insecure_channel(target)

    return grpc.secure_channel(target, _ssl_channel_credentials())


class AuthClient:
    """Thin wrapper around the AuthService gRPC stub.

    Per ES-000 §3: the client never talks to the database directly --
    every operation goes through a stub like this one.
    """

    def __init__(self, host: str | None = None, port: int | None = None):
        target = f"{host or config.GRPC_SERVER_HOST}:{port or config.GRPC_PORT}"
        self._channel = _create_channel(target)
        self._stub = auth_service_pb2_grpc.AuthServiceStub(self._channel)

    def login(self, username: str, password: str) -> auth_service_pb2.LoginResponse:
        try:
            return self._stub.Login(
                auth_service_pb2.LoginRequest(username=username, password=password)
            )
        except grpc.RpcError as exc:
            raise AuthError(exc.code(), exc.details()) from exc

    def logout(self, access_token: str) -> bool:
        try:
            response = self._stub.Logout(
                auth_service_pb2.LogoutRequest(access_token=access_token),
                metadata=(("authorization", f"Bearer {access_token}"),),
            )
            return response.success
        except grpc.RpcError as exc:
            raise AuthError(exc.code(), exc.details()) from exc

    def create_user(
        self, access_token: str, **fields
    ) -> auth_service_pb2.CreateUserResponse:
        try:
            return self._stub.CreateUser(
                auth_service_pb2.CreateUserRequest(**fields),
                metadata=_bearer(access_token),
            )
        except grpc.RpcError as exc:
            raise AuthError(exc.code(), exc.details()) from exc

    def list_users(self, access_token: str) -> auth_service_pb2.ListUsersResponse:
        try:
            return self._stub.ListUsers(
                auth_service_pb2.ListUsersRequest(), metadata=_bearer(access_token)
            )
        except grpc.RpcError as exc:
            raise AuthError(exc.code(), exc.details()) from exc

    def reset_password(
        self, access_token: str, **fields
    ) -> auth_service_pb2.ResetPasswordResponse:
        try:
            return self._stub.ResetPassword(
                auth_service_pb2.ResetPasswordRequest(**fields),
                metadata=_bearer(access_token),
            )
        except grpc.RpcError as exc:
            raise AuthError(exc.code(), exc.details()) from exc


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
        try:
            return self._stub.CreateClient(
                client_service_pb2.CreateClientRequest(**fields),
                metadata=_bearer(access_token),
            )
        except grpc.RpcError as exc:
            raise ApiError(exc.code(), exc.details()) from exc

    def get_client_by_id(
        self, access_token: str, client_id: str
    ) -> client_service_pb2.GetClientByIdResponse:
        try:
            return self._stub.GetClientById(
                client_service_pb2.GetClientByIdRequest(client_id=client_id),
                metadata=_bearer(access_token),
            )
        except grpc.RpcError as exc:
            raise ApiError(exc.code(), exc.details()) from exc

    def search_clients(
        self,
        access_token: str,
        search_term: str = "",
        page_size: int = 0,
        page_token: int = 0,
    ) -> client_service_pb2.SearchClientsResponse:
        try:
            return self._stub.SearchClients(
                client_service_pb2.SearchClientsRequest(
                    search_term=search_term, page_size=page_size, page_token=page_token
                ),
                metadata=_bearer(access_token),
            )
        except grpc.RpcError as exc:
            raise ApiError(exc.code(), exc.details()) from exc

    def update_client(
        self, access_token: str, **fields
    ) -> client_service_pb2.UpdateClientResponse:
        try:
            return self._stub.UpdateClient(
                client_service_pb2.UpdateClientRequest(**fields),
                metadata=_bearer(access_token),
            )
        except grpc.RpcError as exc:
            raise ApiError(exc.code(), exc.details()) from exc

    def deactivate_client(
        self, access_token: str, client_id: str
    ) -> client_service_pb2.DeactivateClientResponse:
        try:
            return self._stub.DeactivateClient(
                client_service_pb2.DeactivateClientRequest(client_id=client_id),
                metadata=_bearer(access_token),
            )
        except grpc.RpcError as exc:
            raise ApiError(exc.code(), exc.details()) from exc

    def update_national_id(
        self, access_token: str, client_id: str, new_national_id: str
    ) -> client_service_pb2.UpdateNationalIdResponse:
        try:
            return self._stub.UpdateNationalId(
                client_service_pb2.UpdateNationalIdRequest(
                    client_id=client_id, new_national_id=new_national_id
                ),
                metadata=_bearer(access_token),
            )
        except grpc.RpcError as exc:
            raise ApiError(exc.code(), exc.details()) from exc


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
        try:
            return self._stub.CreateLoan(
                loan_service_pb2.CreateLoanRequest(**fields),
                metadata=_bearer(access_token),
            )
        except grpc.RpcError as exc:
            raise ApiError(exc.code(), exc.details()) from exc

    def update_loan_proposal(
        self, access_token: str, **fields
    ) -> loan_service_pb2.UpdateLoanProposalResponse:
        try:
            return self._stub.UpdateLoanProposal(
                loan_service_pb2.UpdateLoanProposalRequest(**fields),
                metadata=_bearer(access_token),
            )
        except grpc.RpcError as exc:
            raise ApiError(exc.code(), exc.details()) from exc

    def update_loan_guarantee(
        self, access_token: str, **fields
    ) -> loan_service_pb2.UpdateLoanGuaranteeResponse:
        try:
            return self._stub.UpdateLoanGuarantee(
                loan_service_pb2.UpdateLoanGuaranteeRequest(**fields),
                metadata=_bearer(access_token),
            )
        except grpc.RpcError as exc:
            raise ApiError(exc.code(), exc.details()) from exc

    def update_loan_charges(
        self, access_token: str, **fields
    ) -> loan_service_pb2.UpdateLoanChargesResponse:
        try:
            return self._stub.UpdateLoanCharges(
                loan_service_pb2.UpdateLoanChargesRequest(**fields),
                metadata=_bearer(access_token),
            )
        except grpc.RpcError as exc:
            raise ApiError(exc.code(), exc.details()) from exc

    def update_installment_amount(
        self, access_token: str, **fields
    ) -> loan_service_pb2.UpdateInstallmentAmountResponse:
        try:
            return self._stub.UpdateInstallmentAmount(
                loan_service_pb2.UpdateInstallmentAmountRequest(**fields),
                metadata=_bearer(access_token),
            )
        except grpc.RpcError as exc:
            raise ApiError(exc.code(), exc.details()) from exc

    def get_loan_by_id(
        self, access_token: str, loan_id: str
    ) -> loan_service_pb2.GetLoanByIdResponse:
        try:
            return self._stub.GetLoanById(
                loan_service_pb2.GetLoanByIdRequest(loan_id=loan_id),
                metadata=_bearer(access_token),
            )
        except grpc.RpcError as exc:
            raise ApiError(exc.code(), exc.details()) from exc

    def list_client_loans(
        self, access_token: str, client_id: str
    ) -> loan_service_pb2.ListClientLoansResponse:
        try:
            return self._stub.ListClientLoans(
                loan_service_pb2.ListClientLoansRequest(client_id=client_id),
                metadata=_bearer(access_token),
            )
        except grpc.RpcError as exc:
            raise ApiError(exc.code(), exc.details()) from exc

    def list_active_loans(
        self, access_token: str
    ) -> loan_service_pb2.ListActiveLoansResponse:
        try:
            return self._stub.ListActiveLoans(
                loan_service_pb2.ListActiveLoansRequest(),
                metadata=_bearer(access_token),
            )
        except grpc.RpcError as exc:
            raise ApiError(exc.code(), exc.details()) from exc

    def approve_loan(
        self, access_token: str, loan_id: str
    ) -> loan_service_pb2.ApproveLoanResponse:
        try:
            return self._stub.ApproveLoan(
                loan_service_pb2.ApproveLoanRequest(loan_id=loan_id),
                metadata=_bearer(access_token),
            )
        except grpc.RpcError as exc:
            raise ApiError(exc.code(), exc.details()) from exc

    def disburse_loan(
        self, access_token: str, loan_id: str
    ) -> loan_service_pb2.DisburseLoanResponse:
        try:
            return self._stub.DisburseLoan(
                loan_service_pb2.DisburseLoanRequest(loan_id=loan_id),
                metadata=_bearer(access_token),
            )
        except grpc.RpcError as exc:
            raise ApiError(exc.code(), exc.details()) from exc

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
        try:
            return self._stub.RecordPayment(
                loan_service_pb2.RecordPaymentRequest(
                    loan_id=loan_id,
                    amount=amount,
                    transfer_reference=transfer_reference,
                    installment_number=installment_number,
                    payment_method=payment_method,
                ),
                metadata=_bearer(access_token),
            )
        except grpc.RpcError as exc:
            raise ApiError(exc.code(), exc.details()) from exc

    def mark_defaulted(
        self, access_token: str, loan_id: str
    ) -> loan_service_pb2.MarkDefaultedResponse:
        try:
            return self._stub.MarkDefaulted(
                loan_service_pb2.MarkDefaultedRequest(loan_id=loan_id),
                metadata=_bearer(access_token),
            )
        except grpc.RpcError as exc:
            raise ApiError(exc.code(), exc.details()) from exc

    def get_amortization_schedule(
        self, access_token: str, loan_id: str
    ) -> loan_service_pb2.GetAmortizationScheduleResponse:
        try:
            return self._stub.GetAmortizationSchedule(
                loan_service_pb2.GetAmortizationScheduleRequest(loan_id=loan_id),
                metadata=_bearer(access_token),
            )
        except grpc.RpcError as exc:
            raise ApiError(exc.code(), exc.details()) from exc


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
        try:
            return self._stub.GetDashboardStats(
                dashboard_service_pb2.GetDashboardStatsRequest(),
                metadata=_bearer(access_token),
            )
        except grpc.RpcError as exc:
            raise ApiError(exc.code(), exc.details()) from exc

    def get_period_report(
        self, access_token: str, start_date: str, end_date: str
    ) -> dashboard_service_pb2.GetPeriodReportResponse:
        """BR-DASH-002. `start_date`/`end_date` van en formato de cable
        (YYYY-MM-DD) -- la vista muestra DD/MM/AAAA y traduce con
        formatting.fecha_a_iso() antes de llamar acá."""
        try:
            return self._stub.GetPeriodReport(
                dashboard_service_pb2.GetPeriodReportRequest(
                    start_date=start_date, end_date=end_date
                ),
                metadata=_bearer(access_token),
            )
        except grpc.RpcError as exc:
            raise ApiError(exc.code(), exc.details()) from exc


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
        try:
            return self._stub.OpenCashSession(
                cash_service_pb2.OpenCashSessionRequest(
                    opening_amount=opening_amount, notes=notes
                ),
                metadata=_bearer(access_token),
            )
        except grpc.RpcError as exc:
            raise ApiError(exc.code(), exc.details()) from exc

    def get_current_cash_session(
        self, access_token: str
    ) -> cash_service_pb2.GetCurrentCashSessionResponse:
        try:
            return self._stub.GetCurrentCashSession(
                cash_service_pb2.GetCurrentCashSessionRequest(),
                metadata=_bearer(access_token),
            )
        except grpc.RpcError as exc:
            raise ApiError(exc.code(), exc.details()) from exc

    def register_cash_movement(
        self, access_token: str, movement_type: str, amount: str, concept: str
    ) -> cash_service_pb2.CashSessionDetail:
        try:
            return self._stub.RegisterCashMovement(
                cash_service_pb2.RegisterCashMovementRequest(
                    movement_type=movement_type, amount=amount, concept=concept
                ),
                metadata=_bearer(access_token),
            )
        except grpc.RpcError as exc:
            raise ApiError(exc.code(), exc.details()) from exc

    def close_cash_session(
        self,
        access_token: str,
        counted_amount: str,
        notes: str = "",
        session_id: str = "",
    ) -> cash_service_pb2.CashSessionDetail:
        try:
            return self._stub.CloseCashSession(
                cash_service_pb2.CloseCashSessionRequest(
                    counted_amount=counted_amount, notes=notes, session_id=session_id
                ),
                metadata=_bearer(access_token),
            )
        except grpc.RpcError as exc:
            raise ApiError(exc.code(), exc.details()) from exc

    def list_cash_sessions(
        self, access_token: str, start_date: str = "", end_date: str = ""
    ) -> cash_service_pb2.ListCashSessionsResponse:
        """Dates go in wire format (YYYY-MM-DD) -- the view shows DD/MM/AAAA
        and converts with formatting.fecha_a_iso() before calling here, same
        contract as DashboardServiceClient.get_period_report."""
        try:
            return self._stub.ListCashSessions(
                cash_service_pb2.ListCashSessionsRequest(
                    start_date=start_date, end_date=end_date
                ),
                metadata=_bearer(access_token),
            )
        except grpc.RpcError as exc:
            raise ApiError(exc.code(), exc.details()) from exc
