# CAS Skills Registry

These Skills define how an AI agent must work on the Credit Agency
System.

## Core

-   `sdd` --- Spec-Driven Development workflow.
-   `architecture` --- Client/server and layer boundaries.
-   `domain` --- Business domain integrity.
-   `database` --- PostgreSQL rules and persistence integrity.
-   `financial-rules` --- Monetary and credit calculation safeguards.

## Communication

-   `grpc` --- gRPC service implementation.
-   `protobuf` --- Protocol Buffer contract management.

## Security

-   `authentication` --- Argon2 and JWT authentication.
-   `authorization` --- Roles and permissions.
-   `security` --- General application and LAN security.

## Desktop

-   `pyside6` --- PySide6 desktop application rules.

## Quality

-   `testing` --- Automated testing strategy.
-   `concurrency` --- Multi-client consistency and race-condition
    prevention.

## Skill selection principle

The agent should load all Skills relevant to the current task. Specific
domain Skills take precedence over generic Skills when they contain more
restrictive rules.

Examples:

### Payment

Use: - sdd - domain - financial-rules - database - concurrency - grpc -
protobuf - authorization - auditing, when available - testing

### Database migration

Use: - sdd - architecture - database - testing

### PySide6 screen

Use: - sdd - architecture - pyside6 - grpc - authorization - testing

### Authentication

Use: - sdd - authentication - authorization - security - grpc -
protobuf - testing

### Cash register (caja / arqueo)

Use: - sdd - domain - financial-rules - database - concurrency -
authorization - grpc - protobuf - testing

The domain Skill's Cash Session / Cash Movement section and
`specs/cash/README` are authoritative here. Two traps worth loading
`concurrency` and `authorization` for: the one-open-session-per-cashier
rule is a partial unique index rather than a servicer check, and the
"may close another cashier's caja" rule lives in the servicer rather
than in `rbac.py`.
