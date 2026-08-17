from cas_server.db.models import RoleEnum
from cas_server.security import rbac


def test_login_is_public():
    assert rbac.is_public("/auth.AuthService/Login")


def test_logout_allows_any_authenticated_role():
    assert rbac.allowed_roles("/auth.AuthService/Logout") == frozenset(RoleEnum)


def test_reset_password_is_admin_only():
    assert rbac.allowed_roles("/auth.AuthService/ResetPassword") == frozenset(
        {RoleEnum.ADMIN}
    )


def test_unknown_method_is_denied_by_default():
    assert not rbac.is_public("/auth.AuthService/DoesNotExist")
    assert rbac.allowed_roles("/auth.AuthService/DoesNotExist") == frozenset()


def test_create_client_is_credit_analyst_and_above():
    """BR-CAJA-005: el cajero consulta y cobra, no da de alta clientes."""
    assert rbac.allowed_roles("/clients.ClientService/CreateClient") == frozenset(
        {RoleEnum.CREDIT_ANALYST, RoleEnum.MANAGER, RoleEnum.ADMIN}
    )


def test_client_lookup_still_allows_the_cashier():
    """La contracara de la regla anterior: el cajero tiene que poder buscar a
    un cliente y ver sus cuotas para informarle cuánto debe."""
    for metodo in (
        "/clients.ClientService/SearchClients",
        "/clients.ClientService/GetClientById",
        "/loans.LoanService/GetLoanById",
        "/loans.LoanService/ListClientLoans",
        "/loans.LoanService/GetAmortizationSchedule",
        "/loans.LoanService/RecordPayment",
    ):
        assert RoleEnum.CASHIER in rbac.allowed_roles(metodo), metodo


def test_deactivate_client_is_manager_and_above():
    assert rbac.allowed_roles("/clients.ClientService/DeactivateClient") == frozenset(
        {RoleEnum.MANAGER, RoleEnum.ADMIN}
    )


def test_update_national_id_is_admin_only():
    assert rbac.allowed_roles("/clients.ClientService/UpdateNationalId") == frozenset(
        {RoleEnum.ADMIN}
    )


def test_create_loan_is_credit_analyst_and_above():
    """BR-CAJA-005 -- ver test_create_client_is_credit_analyst_and_above."""
    assert rbac.allowed_roles("/loans.LoanService/CreateLoan") == frozenset(
        {RoleEnum.CREDIT_ANALYST, RoleEnum.MANAGER, RoleEnum.ADMIN}
    )


def test_cash_rpcs_allow_any_authenticated_role():
    """BR-CAJA-*: cada rol opera su propia caja -- el turno sale del token, no
    del request, así que no hace falta restringir el método en sí. Las dos
    asimetrías por rol (cerrar una caja ajena, ver el historial de todos) se
    resuelven dentro de cash_service.py, no acá."""
    for metodo in (
        "/cash.CashService/OpenCashSession",
        "/cash.CashService/GetCurrentCashSession",
        "/cash.CashService/RegisterCashMovement",
        "/cash.CashService/CloseCashSession",
        "/cash.CashService/ListCashSessions",
    ):
        assert rbac.allowed_roles(metodo) == frozenset(RoleEnum), metodo


def test_approve_loan_is_credit_analyst_and_above():
    assert rbac.allowed_roles("/loans.LoanService/ApproveLoan") == frozenset(
        {RoleEnum.CREDIT_ANALYST, RoleEnum.MANAGER, RoleEnum.ADMIN}
    )


def test_mark_defaulted_is_credit_analyst_and_above():
    assert rbac.allowed_roles("/loans.LoanService/MarkDefaulted") == frozenset(
        {RoleEnum.CREDIT_ANALYST, RoleEnum.MANAGER, RoleEnum.ADMIN}
    )


def test_disburse_loan_is_manager_and_above():
    assert rbac.allowed_roles("/loans.LoanService/DisburseLoan") == frozenset(
        {RoleEnum.MANAGER, RoleEnum.ADMIN}
    )
