"""BR-AUTH-004: per-RPC role authorization.

Maps a gRPC full method name (e.g. "/auth.AuthService/Login") to the set of
roles allowed to call it. Methods not listed in either PUBLIC_METHODS or
METHOD_ROLES are denied by default -- new RPCs must be explicitly added here
as new services come online.
"""

from cas_server.db.models import RoleEnum

ALL_ROLES = frozenset(RoleEnum)

# Named role tiers for the client/loan RPC tables below -- each is a superset
# of the one "beneath" it, matching the CASHIER < CREDIT_ANALYST < MANAGER <
# ADMIN seniority implied by specs/clients and specs/loans' role mentions.
CASHIER_AND_ABOVE: frozenset[RoleEnum] = ALL_ROLES
CREDIT_ANALYST_AND_ABOVE: frozenset[RoleEnum] = frozenset(
    {RoleEnum.CREDIT_ANALYST, RoleEnum.MANAGER, RoleEnum.ADMIN}
)
MANAGER_AND_ABOVE: frozenset[RoleEnum] = frozenset({RoleEnum.MANAGER, RoleEnum.ADMIN})

PUBLIC_METHODS: frozenset[str] = frozenset(
    {
        "/auth.AuthService/Login",
    }
)

METHOD_ROLES: dict[str, frozenset[RoleEnum]] = {
    "/auth.AuthService/Logout": ALL_ROLES,
    "/auth.AuthService/ResetPassword": frozenset({RoleEnum.ADMIN}),
    "/auth.AuthService/CreateUser": frozenset({RoleEnum.ADMIN}),
    "/auth.AuthService/ListUsers": frozenset({RoleEnum.ADMIN}),
    # BR-CAJA-005: el cajero es un rol de ventanilla, no de originación. Las
    # altas y ediciones de clientes y de propuestas de crédito subieron a
    # CREDIT_ANALYST_AND_ABOVE cuando se reincorporó el rol; lo que el cajero
    # conserva es la CONSULTA (para poder informarle al cliente cuánto debe) y
    # el cobro. Antes de la caja, CASHIER no estaba asignado a nadie y estos
    # métodos estaban en CASHIER_AND_ABOVE por defecto -- ahora el rol se usa
    # de verdad, así que el límite tiene que ser real.
    "/clients.ClientService/CreateClient": CREDIT_ANALYST_AND_ABOVE,
    "/clients.ClientService/GetClientById": CASHIER_AND_ABOVE,
    "/clients.ClientService/SearchClients": CASHIER_AND_ABOVE,
    "/clients.ClientService/UpdateClient": CREDIT_ANALYST_AND_ABOVE,
    "/clients.ClientService/DeactivateClient": MANAGER_AND_ABOVE,  # BR-CLI-004
    "/clients.ClientService/UpdateNationalId": frozenset(
        {RoleEnum.ADMIN}
    ),  # BR-CLI-003
    "/loans.LoanService/CreateLoan": CREDIT_ANALYST_AND_ABOVE,
    "/loans.LoanService/UpdateLoanProposal": CREDIT_ANALYST_AND_ABOVE,  # BR-LOAN-004
    "/loans.LoanService/UpdateLoanGuarantee": CREDIT_ANALYST_AND_ABOVE,  # BR-LOAN-005
    "/loans.LoanService/UpdateLoanCharges": CREDIT_ANALYST_AND_ABOVE,  # BR-LOAN-006
    "/loans.LoanService/GetLoanById": CASHIER_AND_ABOVE,
    "/loans.LoanService/ListClientLoans": CASHIER_AND_ABOVE,
    "/loans.LoanService/ListActiveLoans": CASHIER_AND_ABOVE,
    "/loans.LoanService/RecordPayment": CASHIER_AND_ABOVE,
    "/loans.LoanService/GetAmortizationSchedule": CASHIER_AND_ABOVE,
    "/loans.LoanService/UpdateInstallmentAmount": MANAGER_AND_ABOVE,
    "/loans.LoanService/ApproveLoan": CREDIT_ANALYST_AND_ABOVE,
    "/loans.LoanService/MarkDefaulted": CREDIT_ANALYST_AND_ABOVE,
    "/loans.LoanService/DisburseLoan": MANAGER_AND_ABOVE,
    # BR-LOAN-012: borrar un préstamo cargado por error es la única operación
    # que elimina una fila en vez de moverle el estado, así que se gatea más
    # arriba que la aprobación (CREDIT_ANALYST_AND_ABOVE) -- deshacer el
    # trabajo de otro es supervisión, no originación. El servicer restringe
    # además *qué* préstamos, no solo quién: ver _ESTADOS_ELIMINABLES.
    "/loans.LoanService/DeleteLoan": MANAGER_AND_ABOVE,
    "/dashboard.DashboardService/GetDashboardStats": CASHIER_AND_ABOVE,
    # BR-DASH-002: el reporte de cierre de período es material de gestión
    # (resultados del mes/trimestre), no una consulta operativa -- se limita a
    # MANAGER+ en vez de seguir a GetDashboardStats, que sí es CASHIER_AND_ABOVE
    # por ser solo conteos de la pantalla de inicio.
    "/dashboard.DashboardService/GetPeriodReport": MANAGER_AND_ABOVE,
    # BR-CAJA-*. Todas son CASHIER_AND_ABOVE porque el turno sobre el que
    # operan se resuelve desde el token, no desde el request: cada rol opera
    # su propia caja. Las dos asimetrías por rol (un Gerente puede cerrar la
    # caja de otro y ve el historial de todos los cajeros) NO se expresan acá
    # sino dentro de cash_service.py, porque no son "puede o no llamar al
    # método" sino "sobre qué filas actúa" -- ver _es_supervisor().
    "/cash.CashService/OpenCashSession": CASHIER_AND_ABOVE,
    "/cash.CashService/GetCurrentCashSession": CASHIER_AND_ABOVE,
    "/cash.CashService/RegisterCashMovement": CASHIER_AND_ABOVE,
    "/cash.CashService/CloseCashSession": CASHIER_AND_ABOVE,
    "/cash.CashService/ListCashSessions": CASHIER_AND_ABOVE,
}


def is_public(method: str) -> bool:
    return method in PUBLIC_METHODS


def allowed_roles(method: str) -> frozenset[RoleEnum]:
    """Roles allowed to call `method`. Empty set => deny (method unknown/unlisted)."""
    return METHOD_ROLES.get(method, frozenset())
