# Skill: Concurrency --- CAS

## Purpose

Protect data integrity when two or more desktop clients operate
simultaneously.

## High-risk operations

-   Payments.
-   Credit approval.
-   Credit disbursement.
-   Cash opening/closing.
-   Balance updates.
-   Refinancing.
-   Reversals.

## Rules

-   Assume operations can occur simultaneously.
-   Use database transactions for multi-step mutations.
-   Use row-level locking where required.
-   Evaluate race conditions for every critical mutation.
-   Design idempotency for operations where retries could duplicate
    effects.
-   Never trust a previously-read balance when applying a financial
    mutation without revalidation inside the transaction.
-   Add concurrency tests for critical workflows.

## Example risk

Two clients attempt to register payments against the same balance at the
same time. The server must prevent an inconsistent or negative balance.

## Forbidden

-   Client-side locking as the only protection.
-   Assuming WiFi latency prevents concurrent operations.
-   Updating balances outside a transaction.
