# 0013 — Invalidation advances a per-environment generation, then bumps `config_version`

**Status:** Accepted. It replaces "evict the keys and bump `config_version` together".

## Context

The first design deleted the affected keys and bumped `Environment.config_version`, the
ETag of `GET /sdk/flags/config/`. Deleting keys cannot keep a cache-aside cache coherent.
A reader that loaded a row *before* a write can `set` it *after* the write's eviction.
That stale copy is then served for the full TTL, and baked into the config download under
the new `config_version`, where the SDK's ETag pins it.
`TestConfigDownloadAfterConcurrentKillSwitch` reproduces this.

## Decision

Each environment has a generation in Redis (`flaggen:{project_id}:{env_id}`), and it is
part of every payload key.

- A reader takes the generation **before** touching Postgres and caches under it.
- A writer advances it **after** COMMIT and **before** bumping `config_version`.

`FlagService._advance_and_bump` is the single chokepoint that does both, in that order.

## Consequences

- A change to any flag invalidates every flag cached in that environment. This is
  deliberate: writes are rare next to reads, and a segment fan-out then costs one advance
  per environment.
- Never invalidate inside `transaction.atomic()`.
- Never bump without advancing, and never bump first.
