# 0017 — Numeric constraints are validated at three layers

**Status:** Accepted

## Context

A `rollout_percentage` outside 0–100 corrupts bucketing. Writes reach the database
through the API, the admin and the ORM directly.

## Decision

`rollout_percentage` (0–100), on both `FeatureFlag` and `Rule`, is enforced at the DRF
serializer, the Django model validator, and a PostgreSQL `CheckConstraint`.

## Consequences

- Each write path gets a sensible error, and the database is the backstop.
- Every new numeric constraint follows the same pattern.
