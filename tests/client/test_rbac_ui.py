from decimal import Decimal

from cas_client.rbac_ui import (
    can_revert_default,
    FIXED_INTEREST_RATE,
    MAX_CHARGES_RATIO,
    can_delete_client,
    can_delete_loan,
    can_edit_installment_amount,
    can_edit_interest_rate,
    can_manage_users,
    can_originate_credit,
    can_supervise_cash_sessions,
    can_view_payment_status_report,
    can_view_period_report,
    is_teller,
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
    # BR-CAJA-005: CASHIER dejó de estar agrupado bajo "Estándar" -- volvió a
    # asignarse a personas reales y tiene un conjunto de permisos propio, así
    # que mostrarlo como "Estándar" describiría mal lo que ese usuario puede
    # hacer.
    assert tier_label("CASHIER") == "Cajero"
    assert tier_label("CREDIT_ANALYST") == "Estándar"
    assert tier_label("MANAGER") == "Agente de Créditos"
    assert tier_label("ADMIN") == "Administrador"


def test_tier_label_unknown_role_returns_desconocido():
    assert tier_label("NOT_A_ROLE") == "Desconocido"
    assert tier_label(None) == "Desconocido"


def test_nobody_can_edit_the_interest_rate():
    """BR-LOAN-007: la tasa es fija para todos desde 2026-08-28 -- incluido el
    Administrador, que hasta entonces sí podía escribir otra."""
    for role in ("CASHIER", "CREDIT_ANALYST", "MANAGER", "ADMIN", None):
        assert not can_edit_interest_rate(role)


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
    assert FIXED_INTEREST_RATE == "0.20"  # 20% anual, tope legal = 1,667% mensual


def test_max_charges_ratio_matches_server_side_constant():
    # BR-LOAN-006 (revisado 2026-09-08): cas_server/config.py's
    # LOAN_MAX_CHARGES_RATIO must stay in sync with this UI-side constant by
    # hand, same caveat as FIXED_INTEREST_RATE above. 40% is the complement of
    # the 20% legal interest cap to reach the entity's 60% total (subió de
    # 25%/45% por decisión del negocio).
    assert MAX_CHARGES_RATIO == Decimal("0.40")


def test_is_teller_only_matches_the_cashier_role():
    """No es "por debajo de Analista de Crédito": el Cajero es un puesto con
    su propia navegación, no un piso de jerarquía."""
    assert is_teller("CASHIER")
    assert not is_teller("CREDIT_ANALYST")
    assert not is_teller("MANAGER")
    assert not is_teller("ADMIN")
    assert not is_teller(None)


def test_can_supervise_cash_sessions_only_manager_and_above():
    """Espeja cash_service.py's _es_supervisor(): ver los arqueos de todos los
    cajeros y cerrar una caja ajena."""
    assert not can_supervise_cash_sessions("CASHIER")
    assert not can_supervise_cash_sessions("CREDIT_ANALYST")
    assert can_supervise_cash_sessions("MANAGER")
    assert can_supervise_cash_sessions("ADMIN")


def test_can_originate_credit_matches_the_server_side_gate():
    """Guarda de sincronización real contra METHOD_ROLES, igual que la de
    GetPeriodReport de más abajo: BR-CAJA-005 subió el alta de clientes y de
    préstamos a CREDIT_ANALYST_AND_ABOVE, y ocultar/mostrar esos botones en la
    UI tiene que seguir esa tabla, no una copia a mano."""
    from cas_server.security.rbac import allowed_roles

    for metodo in (
        "/clients.ClientService/CreateClient",
        "/clients.ClientService/UpdateClient",
        "/loans.LoanService/CreateLoan",
        "/loans.LoanService/UpdateLoanProposal",
    ):
        permitidos = allowed_roles(metodo)
        for role in ("CASHIER", "CREDIT_ANALYST", "MANAGER", "ADMIN"):
            esperado = any(permitido.value == role for permitido in permitidos)
            assert can_originate_credit(role) == esperado, (metodo, role)


def test_cashier_keeps_the_lookup_and_collection_methods():
    """La contracara del test anterior: lo que el Cajero sí conserva. Si
    alguna de estas subiera de nivel, su pantalla quedaría sin poder informar
    ni cobrar, que es justamente su trabajo."""
    from cas_server.security.rbac import allowed_roles

    for metodo in (
        "/clients.ClientService/SearchClients",
        "/clients.ClientService/GetClientById",
        "/loans.LoanService/GetLoanById",
        "/loans.LoanService/ListClientLoans",
        "/loans.LoanService/GetAmortizationSchedule",
        "/loans.LoanService/RecordPayment",
        "/cash.CashService/OpenCashSession",
        "/cash.CashService/RegisterCashMovement",
        "/cash.CashService/CloseCashSession",
    ):
        permitidos = {permitido.value for permitido in allowed_roles(metodo)}
        assert "CASHIER" in permitidos, metodo


def test_period_report_gate_matches_rbac_tables_exactly():
    """Guarda de sincronización real, no una copia a mano de la expectativa:
    compara can_view_period_report() contra la tabla METHOD_ROLES que el
    servidor consulta de verdad, para el mismo método."""
    from cas_server.security.rbac import allowed_roles

    permitidos = allowed_roles("/dashboard.DashboardService/GetPeriodReport")
    for role in ("CASHIER", "CREDIT_ANALYST", "MANAGER", "ADMIN"):
        esperado = any(permitido.value == role for permitido in permitidos)
        assert can_view_period_report(role) == esperado, role


def test_delete_loan_gate_matches_rbac_tables_exactly():
    """BR-LOAN-012. Misma guarda de sincronizacion que el reporte de periodo:
    se compara contra la tabla METHOD_ROLES real en vez de repetir a mano la
    expectativa, que es justamente lo que se desincroniza."""
    from cas_server.security.rbac import allowed_roles

    permitidos = allowed_roles("/loans.LoanService/DeleteLoan")
    for role in ("CASHIER", "CREDIT_ANALYST", "MANAGER", "ADMIN"):
        esperado = any(permitido.value == role for permitido in permitidos)
        assert can_delete_loan(role) == esperado, role


def test_delete_loan_excludes_only_the_teller():
    """El borrado acompana a la originacion: quien puede cargar un prestamo
    puede deshacer su propia carga. El unico rol sin el permiso es el cajero
    (BR-CAJA-005), que ni origina ni deshace originacion."""
    assert can_delete_loan("CASHIER") is False
    assert can_delete_loan("CREDIT_ANALYST") is True
    assert can_delete_loan("MANAGER") is True
    assert can_delete_loan("ADMIN") is True
    assert can_delete_loan(None) is False


def test_delete_loan_matches_originating_credit():
    """Las dos puertas coinciden a proposito desde este cambio -- si alguna
    vuelve a moverse sin la otra, esto lo marca."""
    for role in ("CASHIER", "CREDIT_ANALYST", "MANAGER", "ADMIN"):
        assert can_delete_loan(role) == can_originate_credit(role), role


def test_delete_client_gate_matches_rbac_tables_exactly():
    """BR-CLI-008. Misma guarda de sincronizacion que el resto: se compara
    contra la tabla METHOD_ROLES real en vez de repetir a mano la
    expectativa."""
    from cas_server.security.rbac import allowed_roles

    permitidos = allowed_roles("/clients.ClientService/DeleteClient")
    for role in ("CASHIER", "CREDIT_ANALYST", "MANAGER", "ADMIN"):
        esperado = any(permitido.value == role for permitido in permitidos)
        assert can_delete_client(role) == esperado, role


def test_delete_client_excludes_only_the_teller():
    """El unico rol sin el permiso es el cajero (BR-CAJA-005), que ni origina
    ni deshace originacion de clientes."""
    assert can_delete_client("CASHIER") is False
    assert can_delete_client("CREDIT_ANALYST") is True
    assert can_delete_client("MANAGER") is True
    assert can_delete_client("ADMIN") is True
    assert can_delete_client(None) is False


def test_delete_client_matches_originating_credit():
    """Mismo criterio que can_delete_loan(): quien puede dar de alta/editar
    un cliente es quien puede deshacer su propia carga."""
    for role in ("CASHIER", "CREDIT_ANALYST", "MANAGER", "ADMIN"):
        assert can_delete_client(role) == can_originate_credit(role), role


def test_payment_status_report_gate_matches_rbac_tables_exactly():
    """BR-DASH-003. Misma guarda de sincronización que el reporte de período:
    se compara contra la tabla METHOD_ROLES real en vez de repetir a mano la
    expectativa."""
    from cas_server.security.rbac import allowed_roles

    permitidos = allowed_roles(
        "/dashboard.DashboardService/GetClientPaymentStatusReport"
    )
    for role in ("CASHIER", "CREDIT_ANALYST", "MANAGER", "ADMIN"):
        esperado = any(permitido.value == role for permitido in permitidos)
        assert can_view_payment_status_report(role) == esperado, role


def test_payment_status_report_is_gated_lower_than_the_period_report():
    """La cobranza la trabaja el Analista de Crédito; el cierre de período es
    material de gerencia. Si los dos gates se igualaran, uno de los dos
    estaría mal."""
    assert can_view_payment_status_report("CREDIT_ANALYST")
    assert not can_view_period_report("CREDIT_ANALYST")
    assert not can_view_payment_status_report("CASHIER")


def test_revert_default_gate_matches_rbac_tables_exactly():
    """BR-LOAN-014. Misma guarda de sincronización que las de arriba: se
    compara contra la tabla METHOD_ROLES real en vez de repetir a mano la
    expectativa."""
    from cas_server.security.rbac import allowed_roles

    permitidos = allowed_roles("/loans.LoanService/RevertDefault")
    for role in ("CASHIER", "CREDIT_ANALYST", "MANAGER", "ADMIN"):
        esperado = any(permitido.value == role for permitido in permitidos)
        assert can_revert_default(role) == esperado, role


def test_revert_default_is_gated_exactly_like_mark_defaulted():
    """BR-LOAN-014: quien puede poner la marca de incumplimiento puede sacarla.
    Si los dos gates se separaran, un operador podría marcar un préstamo como
    incumplido y quedar sin poder deshacerlo -- que es el callejón sin salida
    que esta regla vino a cerrar."""
    from cas_server.security.rbac import allowed_roles

    assert allowed_roles("/loans.LoanService/RevertDefault") == allowed_roles(
        "/loans.LoanService/MarkDefaulted"
    )


def test_remove_installment_adjustment_is_gated_exactly_like_updating_it():
    """BR-LOAN-015: quien puede fijar el monto de una cuota puede devolverla al
    calculado. Si los dos gates se separaran, un operador podría ajustar una
    cuota y quedar sin poder deshacerlo -- el mismo callejón sin salida que
    BR-LOAN-014 cerró para el incumplimiento, y la razón de existir de esta
    RPC."""
    from cas_server.security.rbac import allowed_roles

    assert allowed_roles(
        "/loans.LoanService/RemoveInstallmentAdjustment"
    ) == allowed_roles("/loans.LoanService/UpdateInstallmentAmount")


def test_remove_installment_adjustment_gate_matches_rbac_tables_exactly():
    """El botón "Quitar" del cronograma se muestra bajo la misma condición que
    "Ajustar" (can_edit_installment_amount), así que esa función tiene que
    seguir coincidiendo con la tabla METHOD_ROLES real de la RPC nueva."""
    from cas_server.security.rbac import allowed_roles

    permitidos = allowed_roles("/loans.LoanService/RemoveInstallmentAdjustment")
    for role in ("CASHIER", "CREDIT_ANALYST", "MANAGER", "ADMIN"):
        esperado = any(permitido.value == role for permitido in permitidos)
        assert can_edit_installment_amount(role) == esperado, role
