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


def test_create_client_allows_any_authenticated_role():
    assert rbac.allowed_roles("/clients.ClientService/CreateClient") == frozenset(
        RoleEnum
    )


def test_deactivate_client_is_manager_and_above():
    assert rbac.allowed_roles("/clients.ClientService/DeactivateClient") == frozenset(
        {RoleEnum.MANAGER, RoleEnum.ADMIN}
    )


def test_update_national_id_is_admin_only():
    assert rbac.allowed_roles("/clients.ClientService/UpdateNationalId") == frozenset(
        {RoleEnum.ADMIN}
    )


def test_create_loan_allows_any_authenticated_role():
    assert rbac.allowed_roles("/loans.LoanService/CreateLoan") == frozenset(RoleEnum)


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
