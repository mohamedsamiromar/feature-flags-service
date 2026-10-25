# 0010 — Archive (soft-delete) instead of hard-delete

**Status:** Accepted

## Context

Hard-deleting a flag destroys its audit history, evaluation logs and rule configuration.

## Decision

Flags are archived. An archived flag disappears from list responses and from evaluation,
but keeps all of its data.

## Consequences

- Unarchive is a zero-loss, one-call restore.
- An archived flag returns 404 from the SDK endpoints and 409 (`FLAG_ARCHIVED`) on any
  mutation.
