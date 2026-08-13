# Skill: Financial Rules --- CAS

## Purpose

Protect financial calculations and state transitions.

## Rules

-   Never use `float`/`double` for monetary calculations.
-   Use `Decimal` with a documented precision and rounding policy.
-   Currency must be explicit.
-   Interest calculation formulas must come from specifications.
-   Late-payment rules must be explicit; never invent them.
-   Payments must be validated against outstanding balance according to
    the specification.
-   Partial payments, overpayments, reversals and refinancing must have
    explicit rules.
-   Every financial mutation must be transactional.
-   Financial operations must be auditable.

## Required validation areas

-   Principal.
-   Interest.
-   Fees.
-   Late interest.
-   Installment amount.
-   Due dates.
-   Outstanding balance.
-   Payment allocation.
-   Rounding.

## Forbidden

-   Arbitrary rounding.
-   Floating-point arithmetic.
-   Changing balances without an auditable transaction.
-   Applying undocumented financial rules.
