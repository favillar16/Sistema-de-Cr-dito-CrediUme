# Skill: Architecture --- CAS

## Purpose

Preserve the desktop client-server architecture of CAS.

## Target architecture

PySide6 Client → gRPC / Protocol Buffers → CAS Server →
Application/Domain → Repository → PostgreSQL

## Rules

-   `cas_client` must never access PostgreSQL directly.
-   `cas_client` must not import SQLAlchemy.
-   `cas_server` must not depend on PySide6.
-   Business rules must not live in UI code or gRPC transport handlers.
-   Persistence concerns must stay behind repository/application
    boundaries.
-   Keep transport, application, domain and infrastructure
    responsibilities separated.
-   Prefer dependency injection and explicit interfaces.
-   Avoid unnecessary frameworks and architectural complexity.

## LAN model

-   One machine runs PostgreSQL and `cas_server`.
-   Other machines connect through gRPC over the local network.
-   The server is the single authority for persistent state.

## Forbidden

-   HTTP/REST endpoints unless explicitly specified.
-   Direct database connections from clients.
-   Duplicating authoritative business data in the client.
-   Moving business calculations into widgets.
