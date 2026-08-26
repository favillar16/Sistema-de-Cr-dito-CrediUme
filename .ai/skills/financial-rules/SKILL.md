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

## Amortisation system in force

`BR-LOAN-013` (`specs/loans/README`, implemented in
`cas_server/services/amortization.py`): the schedule is the **German**
system **as this entity defines it**, and it is neither French nor the
textbook German system. Both components of the installment are
constant:

-   Principal: `principal / term` per installment.
-   Interest: `principal * annual_rate / 12` per installment, computed
    **on the loan's original principal, never on the declining
    balance**.

So every installment is equal (except the last, which absorbs the
rounding cents of the principal), and the loan's total interest is
`principal * monthly_rate * term`.

**Do not "fix" the interest to run on the outstanding balance.** That
is the textbook German system, it produces a decreasing installment, and
it was ruled out explicitly by the entity — on a 7.000.000 Gs / 12-month
loan the difference is 3.150.000 Gs of interest versus 1.706.250 Gs.
Equally, do not mistake this for the French system: there the
*installment* is constant but the interest inside it decreases while the
principal portion grows.

Two consequences that are easy to get wrong when touching this:

-   `BR-LOAN-002`'s 40%-of-income cap is measured against
    `cronograma[0]` — with equal installments that is simply the
    representative one.
-   The signed instruments (Pagaré/Contrato) declare "cuotas iguales"
    and an interest "calculado sobre el monto original del préstamo".
    They must never say "sobre saldos deudores" — that describes a
    different, cheaper calculation than the schedule the same borrower
    signs. Guarded by `tests/client/test_documents_identity.py`.

The standard rate is `config.LOAN_FIXED_INTEREST_RATE` — **45% nominal
annual = 3,75% monthly on the original principal** (`BR-LOAN-007`). It
is a commercial/legal decision, not a code tweak: changing it also moves
`cas_client/rbac_ui.py`'s `FIXED_INTEREST_RATE` (no shared source
between the two processes) and the interest clause in
`cas_client/documents.py`.

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
