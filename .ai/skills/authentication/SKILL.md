# Skill: Authentication --- CAS

## Purpose

Secure user identity and session authentication.

## Current policy

-   Password hashing: Argon2.
-   Authentication token: JWT.
-   Access token lifetime: 8 hours.
-   Refresh token: not currently used.

## Rules

-   Never store plaintext passwords.
-   Never log passwords, tokens or secrets.
-   Validate token signature, expiration and required claims.
-   Keep JWT secrets outside source code.
-   Authentication failures must not reveal sensitive account details.
-   Account status must be checked before authorizing protected
    operations.
-   Token revocation/session invalidation strategy must be documented.

## Security note

The 8-hour token lifetime is an explicit project decision, not a
security guarantee. If early revocation is required, implement a
documented server-side strategy.

## Forbidden

-   Plaintext password storage.
-   Tokens embedded in source code.
-   Long-lived tokens without specification.
