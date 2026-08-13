# Skill: PySide6 --- CAS

## Purpose

Build the desktop UI without coupling presentation to business logic.

## Rules

-   Use PySide6/Qt idioms: signals, slots, models/views and dialogs.
-   Keep long-running operations off the UI thread.
-   gRPC calls must not freeze the GUI.
-   Present server/domain errors as user-friendly messages.
-   UI must consume server responses rather than reproduce authoritative
    business calculations.
-   Use `QTableView`/model-view patterns for data-heavy screens where
    appropriate.
-   Keep reusable widgets/components modular.
-   Respect application-wide visual conventions.

## Forbidden

-   SQLAlchemy imports in client UI code.
-   PostgreSQL connections from the client.
-   Financial calculations embedded in widgets.
-   Blocking network calls on the GUI thread.
