# Skill: Security --- CAS

## Purpose

Apply security controls appropriate for a local financial desktop
system.

## Rules

-   Secrets belong in environment/configuration, not source code.
-   Passwords use Argon2.
-   JWT secrets must be protected.
-   Never log credentials, tokens or sensitive personal data
    unnecessarily.
-   Validate all external input at server boundaries.
-   Apply least privilege to database and OS accounts.
-   Protect document storage with appropriate filesystem permissions.
-   Backups containing personal/financial data must be protected.
-   TLS may be deferred for development only; production use requires an
    explicit security decision.

## Network policy

Current development may use insecure gRPC only on a controlled LAN as an
explicit temporary decision.

## Forbidden

-   Treating LAN access as automatically trusted.
-   Disabling authentication for convenience.
-   Committing `.env` secrets.
-   Returning internal stack traces to clients.
