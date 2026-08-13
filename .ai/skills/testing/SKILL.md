# Skill: Testing --- CAS

## Purpose

Provide confidence in business correctness and client-server
integration.

## Stack

-   pytest
-   Real PostgreSQL for persistence/integration tests
-   Black
-   Flake8

## Rules

-   Do not replace PostgreSQL with SQLite for database tests.
-   Unit tests must cover domain invariants.
-   Integration tests must validate real persistence behavior.
-   gRPC tests must validate request/response and error behavior.
-   Critical financial operations require regression tests.
-   Concurrency-sensitive operations require concurrency tests.
-   Tests should be deterministic and isolated.
-   Bug fixes should include a regression test when practical.

## Minimum test layers

1.  Domain/unit.
2.  Repository/database integration.
3.  gRPC integration.
4.  End-to-end critical workflows.

## Forbidden

-   Declaring success because a function runs manually.
-   Ignoring failing tests.
-   Mocking away the database when testing persistence behavior.
