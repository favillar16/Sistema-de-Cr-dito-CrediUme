# Skill: Protocol Buffers --- CAS

## Purpose

Maintain stable, explicit contracts in `protos/*.proto`.

## Rules

-   Use package versioning such as `cas.v1`.
-   Never reuse a field number after removal.
-   Avoid changing the semantic meaning of an existing field.
-   Prefer additive, backward-compatible changes.
-   Separate contracts by bounded domain/module when useful.
-   Use explicit request/response messages.
-   Do not use `float`/`double` for money.
-   Regenerate Python stubs after contract changes.
-   Update contract tests and affected client/server code.

## Monetary values

Define a canonical money representation before implementing financial
RPCs.

## Forbidden

-   Editing generated stubs manually.
-   Reusing deprecated field numbers.
-   Changing a field's meaning silently.
-   Mixing transport concerns with business logic.
