# CAS Skill Loading Policy

## Objective

Ensure the AI agent loads the appropriate Skills before modifying the
project.

## Mandatory behavior

Before changing code, the agent must: 1. Identify the affected module.
2. Read the applicable specification in `specs/`. 3. Read the relevant
project documents in `docs/`. 4. Select applicable Skills from
`.ai/skills/`. 5. Respect the most restrictive applicable rule. 6.
Implement only the requested scope. 7. Run the required validation.

## Priority

1.  Explicit system/developer instructions.
2.  Approved CAS specifications.
3.  Security and financial invariants.
4.  Architecture rules.
5.  Applicable Skills.
6.  Existing implementation.
7.  Developer convenience.

## Rule conflict

If two project documents conflict, do not silently choose one. Report
the conflict and request a specification decision unless the
higher-priority rule resolves it.

## No silent assumptions

When a requirement is missing, especially for: - interest calculation, -
payment allocation, - rounding, - authorization, - token revocation, -
concurrency, - document retention,

the agent must flag the ambiguity instead of inventing a business rule.
