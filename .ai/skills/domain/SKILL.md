# Skill: Domain --- CAS

## Purpose

Protect the business domain from UI, transport and persistence concerns.

## Core entities

Client, User, Loan, Installment, Payment, Document and Audit Entry.

Loans do not have a separate Guarantor entity: `BR-LOAN-005` implements
the guarantee as flat, nullable columns on `Loan`
(`guarantee_type`/`guarantee_amount`, one guarantee per loan), not a
child table.

There is no Cash Register entity. Cash handling was explicitly dropped
from the system; all collection goes through transfer, direct debit or
account discount, recorded via `LoanPayment.transfer_reference`
(required, non-empty, on `RecordPayment`). Do not reintroduce a cash
register concept without an explicit specification change.

## Rules

-   Business invariants belong in the domain/application layer.
-   Domain operations must be deterministic and testable.
-   Money must use `Decimal`, never binary floating-point arithmetic.
-   State transitions must be explicit and validated.
-   Invalid transitions must fail with meaningful domain errors.
-   Domain code must not depend on PySide6.
-   Domain code should not depend on gRPC generated classes.

## Typical state transitions

Loan (`LoanStatusEnum`): PENDING → APPROVED → ACTIVE → PAID

Alternative terminal states: DEFAULTED, EXPIRED (an undisbursed
APPROVED loan expires after 30 days, `BR-LOAN-003`, checked lazily on
read/write rather than via a scheduler).

There is no DRAFT state, and no REJECTED/CANCELLED/REFINANCED terminal
states in the current implementation.

## Forbidden

-   SQL queries inside domain objects.
-   UI calls from domain code.
-   Silent correction of invalid financial data.
-   Implicit state transitions.
