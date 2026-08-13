# Skill: Authorization --- CAS

## Purpose

Control what authenticated users are allowed to do.

## Principle

Authentication answers WHO the user is. Authorization answers WHAT the
user may do.

## Current implemented model

CAS does **not** use a granular per-action permission table
(`clients.create`, `loans.approve`, etc.). It uses **role tiers**
mapped per RPC method:

-   `cas_server/security/rbac.py` defines `RoleEnum` (`CASHIER`,
    `CREDIT_ANALYST`, `MANAGER`, `ADMIN`) and named tier sets
    (`CASHIER_AND_ABOVE`, `CREDIT_ANALYST_AND_ABOVE`,
    `MANAGER_AND_ABOVE`) that mirror seniority order.
-   Every RPC must have an explicit entry in `PUBLIC_METHODS` or
    `METHOD_ROLES`; an RPC with no entry is unreachable by default
    (deny-by-default), not open by accident.
-   `AuthInterceptor` (`cas_server/security/interceptor.py`) enforces
    this on every call by checking the JWT's role against
    `rbac.allowed_roles(method)`.
-   `cas_client/rbac_ui.py`'s `role_at_least()` mirrors these tiers by
    hand for UI gating only (no shared source with the server) — e.g.
    disbursement needs MANAGER+, approval needs CREDIT_ANALYST+.
-   The `CASHIER` role still exists in `RoleEnum` but is not currently
    assigned to any real user; `CREDIT_ANALYST` is the de facto base
    tier (cash handling was dropped, see `domain` skill).

Do not introduce a separate fine-grained permission table
(`clients.create`, `cash.open`, etc.) without an explicit
specification change — it would duplicate/conflict with `rbac.py`'s
existing tier model.

## Rules

-   Authorization must be enforced on the server (`rbac.py` +
    `AuthInterceptor`), never only in the client.
-   UI permission checks (`rbac_ui.py`) are presentation aids only.
-   Denied operations must be rejected server-side.
-   New RPCs must be added to `rbac.py`'s `PUBLIC_METHODS`/
    `METHOD_ROLES`; when adding a client-side gate, keep `rbac_ui.py`
    in sync with `rbac.py` by hand.
-   Critical operations require explicit authorization at the correct
    tier, reusing the existing `*_AND_ABOVE` sets rather than writing
    ad hoc role sets per method.

## Forbidden

-   Trusting the client to enforce permissions.
-   Role checks only in PySide6.
-   Hard-coded authorization scattered across handlers.
