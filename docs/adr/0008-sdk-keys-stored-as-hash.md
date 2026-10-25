# 0008 — SDK keys are stored as a SHA-256 hash only

**Status:** Accepted

## Context

SDK keys are long-lived credentials. Storing raw values would turn any database breach
into a full key compromise.

## Decision

Only the SHA-256 hash is persisted, and lookup is a single indexed query on it. A 16-char
prefix is stored so users can identify a key. The full key is returned exactly once, on
creation.

## Consequences

- A lost key cannot be recovered, only rotated.
- Hash-only storage means something only while the digest lives in one table. That is why
  `AuditService.REDACTED_FIELDS` strips `hashed_key` from audit snapshots.
