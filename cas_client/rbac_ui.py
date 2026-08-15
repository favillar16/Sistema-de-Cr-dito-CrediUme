"""Client-side role-tier hints, mirroring cas_server/security/rbac.py's tiers.

The server is the sole source of truth for authorization -- this only
decides which actions to show/enable so users aren't offered buttons that
would come back as PERMISSION_DENIED. Keep in sync with rbac.py by hand;
there's no shared source between the two processes.
"""

from decimal import Decimal

ROLE_ORDER = ("CASHIER", "CREDIT_ANALYST", "MANAGER", "ADMIN")


def role_at_least(current_role: str | None, minimum_role: str) -> bool:
    if current_role not in ROLE_ORDER:
        return False
    return ROLE_ORDER.index(current_role) >= ROLE_ORDER.index(minimum_role)


# User-facing "nivel" labels shown in the UI (dashboard, sidebar). The backend
# keeps its 4-role enum (CASHIER/CREDIT_ANALYST/MANAGER/ADMIN) unchanged --
# the CASHIER role simply isn't assigned to anyone right now (cash handling
# was dropped in favor of bank transfer/direct debit, per BR-LOAN policy
# discussion), so it's grouped under "Estándar" defensively rather than left
# unlabeled if it's ever used again.
_TIER_LABELS = {
    "CASHIER": "Estándar",
    "CREDIT_ANALYST": "Estándar",
    "MANAGER": "Agente de Créditos",
    "ADMIN": "Administrador",
}


def tier_label(role: str | None) -> str:
    return _TIER_LABELS.get(role, "Desconocido")


# BR-LOAN-002's 40%-of-income cap is still enforced server-side regardless of
# who sets the rate -- this only controls whether the rate *field* is
# editable in the "Nuevo préstamo" form.
FIXED_INTEREST_RATE = "0.18"  # decimal fraction, sistema francés -- ver loans_view.py
# 18% nominal anual = 1,5% mensual sobre saldos deudores (la tasa compensatoria
# que declaran el Pagaré y el Contrato). El servidor divide por 12 por período.
# Guarded by tests/client/test_rbac_ui.py's test_fixed_interest_rate_matches_
# server_side_constant -- must stay the exact decimal-fraction string
# cas_server/config.py's LOAN_FIXED_INTEREST_RATE compares against.


def fixed_interest_rate_percent() -> str:
    """Percent-entry equivalent of FIXED_INTEREST_RATE for the "Nuevo
    préstamo" form -- the field now takes the rate as a plain percentage
    (the user types 24, not 0.24), so the Estándar-tier prefill needs to
    match that convention: "0.24" -> "24"."""
    percent = Decimal(FIXED_INTEREST_RATE) * 100
    text = f"{percent:f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def can_edit_interest_rate(role: str | None) -> bool:
    """Only Agente de Créditos (MANAGER) and Administrador (ADMIN) can set a
    custom rate; Estándar (CREDIT_ANALYST/CASHIER) gets FIXED_INTEREST_RATE.

    This mirrors a real server-side rule now (BR-LOAN-007): loan_service.py's
    CreateLoan rejects a non-standard interest_rate from any role below
    MANAGER, so hiding/locking the field here is UX, but the boundary itself
    is enforced server-side too.
    """
    return role_at_least(role, "MANAGER")


def can_manage_users(role: str | None) -> bool:
    """Only Administrador (ADMIN) can create users or reset passwords --
    mirrors rbac.py's ADMIN-only gate on CreateUser/ListUsers/ResetPassword."""
    return role_at_least(role, "ADMIN")


def can_view_period_report(role: str | None) -> bool:
    """BR-DASH-002: el reporte de cierre de período es material de gestión, no
    una consulta operativa -- mirrors rbac.py's MANAGER_AND_ABOVE gate on
    GetPeriodReport. Las tarjetas del dashboard (GetDashboardStats) siguen
    siendo visibles para todos los roles."""
    return role_at_least(role, "MANAGER")


def can_edit_installment_amount(role: str | None) -> bool:
    """Only Agente de Créditos (MANAGER) and Administrador (ADMIN) can adjust
    an individual installment's amount (UpdateInstallmentAmount) -- mirrors
    rbac.py's MANAGER_AND_ABOVE gate on that RPC server-side."""
    return role_at_least(role, "MANAGER")
