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


# User-facing "nivel" labels shown in the UI (dashboard, sidebar). These map
# onto the backend's 4-role enum (CASHIER/CREDIT_ANALYST/MANAGER/ADMIN).
#
# CASHIER used to be folded into "Estándar" because nobody had that role --
# cash handling had been dropped in favor of transfer/direct debit. It now
# has its own label again: the establishment reincorporated a teller, and
# BR-CAJA-005 gave the role a real (narrower) permission set, so showing it
# as "Estándar" would misdescribe what that user can actually do.
_TIER_LABELS = {
    "CASHIER": "Cajero",
    "CREDIT_ANALYST": "Estándar",
    "MANAGER": "Agente de Créditos",
    "ADMIN": "Administrador",
}


def tier_label(role: str | None) -> str:
    return _TIER_LABELS.get(role, "Desconocido")


# BR-LOAN-007: la tasa es fija para TODOS los roles desde 2026-08-28. El
# formulario "Nuevo préstamo" ya no tiene campo de tasa -- la muestra como
# dato, no como entrada -- y el servidor rechaza cualquier valor distinto a
# este, venga del rol que venga. Cambiarla es una decisión comercial/legal de
# la entidad: hay que mover también cas_server/config.py's
# LOAN_FIXED_INTEREST_RATE y revisar la cláusula de interés compensatorio de
# cas_client/documents.py.
#
# Revisado 2026-08-28: bajó de 0.45 a 0.20 porque, por ley, el interés en sí
# no puede superar el 20% anual -- el 45% de antes mezclaba interés y gastos
# administrativos en una sola tasa. Ver MAX_CHARGES_RATIO abajo para el
# complemento (25%) que ahora se cobra como cargo financiado, no como
# interés.
FIXED_INTEREST_RATE = "0.20"  # decimal fraction, sistema alemán -- ver loans_view.py
# 20% nominal anual = el máximo que la ley permite cobrar como interés
# (1,667% mensual sobre el monto original del préstamo -- la tasa
# compensatoria que declaran el Pagaré y el Contrato; BR-LOAN-013, el interés
# no se calcula sobre el saldo deudor). El servidor divide por 12 por período.
# Guarded by tests/client/test_rbac_ui.py's test_fixed_interest_rate_matches_
# server_side_constant -- must stay the exact decimal-fraction string
# cas_server/config.py's LOAN_FIXED_INTEREST_RATE compares against.


def fixed_interest_rate_percent() -> str:
    """FIXED_INTEREST_RATE como porcentaje para mostrar en pantalla:
    "0.20" -> "20"."""
    percent = Decimal(FIXED_INTEREST_RATE) * 100
    text = f"{percent:f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


# BR-LOAN-006 (revisado 2026-08-28). Tope conjunto de los 4 cargos
# financiados (impuesto s/intereses, gastos administrativos por desembolso,
# seguro de cancelación, seguros contratados): 25% anual del capital
# solicitado, prorrateado por el plazo igual que el interés. Es el
# complemento de FIXED_INTEREST_RATE para llegar al 45% anual que la entidad
# fija como costo total del crédito (20% de interés legal + hasta 25% de
# gastos administrativos = 45%). Debe mantenerse igual a
# cas_server/config.py's LOAN_MAX_CHARGES_RATIO, por la misma razón que
# FIXED_INTEREST_RATE (no hay fuente compartida entre los dos procesos).
MAX_CHARGES_RATIO = Decimal("0.25")


def can_edit_interest_rate(role: str | None) -> bool:
    """Nadie -- la tasa es fija para todos los roles (BR-LOAN-007).

    Se conserva la función en vez de borrarla porque el rol *sí* sigue siendo
    el eje del resto de los permisos de esta pantalla, y dejar el nombre con
    una respuesta explícita ("no, tampoco el Administrador") es lo que evita
    que alguien vuelva a agregar el campo de tasa suponiendo que se había
    olvidado. Hasta 2026-08-28 devolvía True para MANAGER/ADMIN.
    """
    return False


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


def can_view_payment_status_report(role: str | None) -> bool:
    """BR-DASH-003: el listado de estado de pago de los clientes es material de
    gestión de cobranza -- mirrors rbac.py's CREDIT_ANALYST_AND_ABOVE gate on
    GetClientPaymentStatusReport. Un escalón más abajo que el cierre de
    período (can_view_period_report), que sí es material de gerencia."""
    return role_at_least(role, "CREDIT_ANALYST")


def is_teller(role: str | None) -> bool:
    """El cajero es el único rol con una navegación distinta: su pantalla
    principal es Caja y solo se le ofrece, además, la Consulta de cliente
    (BR-CAJA-005). No es `not role_at_least(role, "CREDIT_ANALYST")` a
    propósito -- es una identidad de puesto, no un piso de jerarquía."""
    return role == "CASHIER"


def can_originate_credit(role: str | None) -> bool:
    """Dar de alta o editar clientes y propuestas de crédito -- refleja el
    salto de esas RPC a CREDIT_ANALYST_AND_ABOVE en rbac.py (BR-CAJA-005).
    El cajero conserva la consulta y el cobro, que son otras RPC."""
    return role_at_least(role, "CREDIT_ANALYST")


def can_supervise_cash_sessions(role: str | None) -> bool:
    """Ver los arqueos de todos los cajeros y cerrar una caja que quedó
    abierta -- mirrors cash_service.py's `_es_supervisor` (MANAGER_AND_ABOVE).
    A diferencia del resto de este módulo, la restricción del servidor no vive
    en rbac.py sino dentro del servicer, porque no es "puede llamar al método"
    sino "sobre qué filas actúa"."""
    return role_at_least(role, "MANAGER")


def can_delete_loan(role: str | None) -> bool:
    """BR-LOAN-012: eliminar un préstamo cargado por error -- mirrors rbac.py's
    CREDIT_ANALYST_AND_ABOVE gate on DeleteLoan. Estuvo un escalón más arriba
    (MANAGER_AND_ABOVE) por considerarse supervisión; por decisión del negocio
    ahora coincide con can_originate_credit(): quien origina puede deshacer su
    propia carga. El único rol excluido es el cajero (BR-CAJA-005), que ni
    origina ni deshace originación. El servidor limita además *qué* préstamos
    son eliminables (solo sin desembolsar y sin pagos), lo que esta función no
    puede saber -- por eso la vista combina las dos condiciones."""
    return role_at_least(role, "CREDIT_ANALYST")


def can_delete_client(role: str | None) -> bool:
    """BR-CLI-008: eliminar un cliente cargado por error -- mirrors rbac.py's
    CREDIT_ANALYST_AND_ABOVE gate on DeleteClient. A propósito el mismo nivel
    que can_originate_credit(): quien da de alta/edita un cliente es quien
    nota el error y no necesita escalar para deshacerlo. A diferencia de
    can_delete_loan(), no hay ningún estado que lo restrinja del lado del
    servidor -- por decisión del negocio, un cliente se borra sin excepción
    (y en cascada con todos sus préstamos), así que esta función es la única
    condición que la vista necesita."""
    return role_at_least(role, "CREDIT_ANALYST")


def can_revert_default(role: str | None) -> bool:
    """BR-LOAN-014: levantar el incumplimiento de un préstamo -- mirrors
    rbac.py's CREDIT_ANALYST_AND_ABOVE gate on RevertDefault. Es a propósito
    el mismo nivel que marca el incumplimiento (`MarkDefaulted`): quien puede
    poner la marca puede sacarla. El servidor limita además *qué* préstamos
    admiten la reversión (solo los DEFAULTED), lo que esta función no puede
    saber -- por eso la vista combina las dos condiciones."""
    return role_at_least(role, "CREDIT_ANALYST")


def can_edit_installment_amount(role: str | None) -> bool:
    """Only Agente de Créditos (MANAGER) and Administrador (ADMIN) can adjust
    an individual installment's amount (UpdateInstallmentAmount) -- mirrors
    rbac.py's MANAGER_AND_ABOVE gate on that RPC server-side."""
    return role_at_least(role, "MANAGER")
