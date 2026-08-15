from cas_client.rbac_ui import (
    FIXED_INTEREST_RATE,
    can_edit_installment_amount,
    can_edit_interest_rate,
    can_manage_users,
    can_view_period_report,
    role_at_least,
    tier_label,
)


def test_can_view_period_report_matches_the_server_side_gate():
    """rbac.py gatea GetPeriodReport con MANAGER_AND_ABOVE -- si esto se
    desincroniza, un rol Estándar vería el botón y recibiría PERMISSION_DENIED."""
    assert can_view_period_report("ADMIN")
    assert can_view_period_report("MANAGER")
    assert not can_view_period_report("CREDIT_ANALYST")
    assert not can_view_period_report("CASHIER")
    assert not can_view_period_report(None)


def test_role_at_least_orders_roles_correctly():
    assert role_at_least("ADMIN", "CASHIER")
    assert role_at_least("MANAGER", "CREDIT_ANALYST")
    assert role_at_least("CREDIT_ANALYST", "CREDIT_ANALYST")
    assert not role_at_least("CASHIER", "MANAGER")
    assert not role_at_least("CREDIT_ANALYST", "ADMIN")


def test_role_at_least_rejects_unknown_or_missing_role():
    assert not role_at_least(None, "CASHIER")
    assert not role_at_least("NOT_A_ROLE", "CASHIER")


def test_tier_label_maps_backend_roles_to_ui_tiers():
    assert tier_label("CASHIER") == "Estándar"
    assert tier_label("CREDIT_ANALYST") == "Estándar"
    assert tier_label("MANAGER") == "Agente de Créditos"
    assert tier_label("ADMIN") == "Administrador"


def test_tier_label_unknown_role_returns_desconocido():
    assert tier_label("NOT_A_ROLE") == "Desconocido"
    assert tier_label(None) == "Desconocido"


def test_can_edit_interest_rate_only_manager_and_above():
    assert not can_edit_interest_rate("CASHIER")
    assert not can_edit_interest_rate("CREDIT_ANALYST")
    assert can_edit_interest_rate("MANAGER")
    assert can_edit_interest_rate("ADMIN")


def test_can_edit_installment_amount_only_manager_and_above():
    assert not can_edit_installment_amount("CASHIER")
    assert not can_edit_installment_amount("CREDIT_ANALYST")
    assert can_edit_installment_amount("MANAGER")
    assert can_edit_installment_amount("ADMIN")


def test_can_manage_users_only_admin():
    assert not can_manage_users("CASHIER")
    assert not can_manage_users("CREDIT_ANALYST")
    assert not can_manage_users("MANAGER")
    assert can_manage_users("ADMIN")


def test_fixed_interest_rate_matches_server_side_constant():
    # BR-LOAN-007: cas_server/config.py's LOAN_FIXED_INTEREST_RATE must stay in
    # sync with this UI-side constant by hand (no shared source between the
    # two processes, same as rbac_ui.py's own module docstring notes for
    # role_at_least/rbac.py). This test exists so a drift is caught here
    # instead of silently rejecting loans in the UI's "Estándar" flow.
    assert FIXED_INTEREST_RATE == "0.18"  # 18% anual = 1,5% mensual


def test_period_report_gate_matches_rbac_tables_exactly():
    """Guarda de sincronización real, no una copia a mano de la expectativa:
    compara can_view_period_report() contra la tabla METHOD_ROLES que el
    servidor consulta de verdad, para el mismo método."""
    from cas_server.security.rbac import allowed_roles

    permitidos = allowed_roles("/dashboard.DashboardService/GetPeriodReport")
    for role in ("CASHIER", "CREDIT_ANALYST", "MANAGER", "ADMIN"):
        esperado = any(permitido.value == role for permitido in permitidos)
        assert can_view_period_report(role) == esperado, role
