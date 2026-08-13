# Skill: Spec-Driven Development (SDD) --- CAS

## Purpose

Control the development workflow so implementation is derived from
explicit specifications rather than assumptions.

## Applies to

All CAS modules, architecture changes, database changes, gRPC contracts,
UI changes and bug fixes.

## Mandatory workflow

1.  Identify the relevant `docs/` and `specs/` documents.
2.  Determine the applicable Skills.
3.  Check for contradictions or missing requirements.
4.  Define or update acceptance criteria.
5.  Design the solution before implementation.
6.  Define or update tests.
7.  Implement the smallest compliant change.
8.  Run validation and regression tests.
9.  Update affected documentation/specifications.
10. Report completed work and unresolved risks.

## Rules

-   Specifications define WHAT the system must do.
-   Skills define HOW the agent must work.
-   Never invent financial or security rules when the specification is
    silent; flag the ambiguity.
-   Do not implement unrelated improvements during a task.
-   Prefer small, traceable changes.
-   Every significant architectural decision must be documented.

## Forbidden

-   Coding before understanding the applicable specification.
-   Silently changing requirements.
-   Implementing features solely because they seem useful.
-   Marking a feature complete without acceptance validation.

## Definition of Done

-   Requirements satisfied.
-   Acceptance criteria covered.
-   Tests pass.
-   Quality checks pass.
-   Documentation is synchronized.
-   No known critical regression remains.
