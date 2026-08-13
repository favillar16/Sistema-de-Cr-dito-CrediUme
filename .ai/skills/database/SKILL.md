# Skill: Database --- CAS

## Purpose

Design and protect PostgreSQL as the single system of record.

## Rules

-   PostgreSQL is authoritative.
-   No SQLite for application persistence.
-   No JSON/file-based replacement for relational data.
-   Use transactions for multi-step business operations.
-   Use foreign keys and database constraints for integrity.
-   Use appropriate indexes for frequent searches.
-   Use `NUMERIC`/SQLAlchemy `Decimal` for monetary values.
-   Use UTC-aware timestamps where appropriate.
-   Prefer UUID identifiers where defined by the domain.
-   Never rely only on application validation for uniqueness or
    referential integrity.

## Concurrency

Critical operations must be safe under simultaneous clients. Consider
row locks and transaction isolation where required.

## Forbidden

-   Destructive schema/data changes without explicit approval.
-   Storing passwords in plain text.
-   Using floating point for monetary persistence.
-   Client-side database access.
