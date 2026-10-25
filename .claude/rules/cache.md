---
paths:
  - "apps/flags/services.py"
  - "apps/environment/services.py"
  - "apps/segments/services.py"
  - "apps/rules/services.py"
  - "apps/evaluation/services.py"
---

# Cache invalidation

Why it works this way: [ADR 0013](../../docs/adr/0013-invalidation-by-generation.md).

## What to call

- After any mutation that changes what a flag serves (flag config, variations, rules,
  targets, prerequisites), call `FlagService.invalidate_flag_caches(flag)`.
- For a mutation affecting many flags at once, such as a segment edit, call
  `FlagService.invalidate_many_flag_caches(flags)`. It resolves every flag's environments
  in **one** query instead of one per flag.
- The cached payload embeds rules, targets, prerequisites, and the resolved segments those
  rules reference. Any stale entry causes incorrect evaluations.

## One key builder

The payload key `flags:{project_id}:{env_id}:{generation}:{flag_key}` is written in exactly
one place, `FlagEvaluationService._cache_key`. Never format it by hand; tests use
`FlagEvaluationService.cache_key_for`. `EnvironmentFlagService` used to format it, and a
second copy of that string is how a pre-tenancy version of this code evicted one key while
evaluation read another.

`config:{project_id}:{env_id}:{config_version}` (the whole config-download payload) is keyed
by version, so it never needs explicit invalidation. A bump moves the key, and the old entry
expires on its own.

## Invalidation is a generation advance, not a delete

Each environment has a cache generation in Redis (`flaggen:{project_id}:{env_id}`), and
every payload key includes it. Deleting keys cannot keep a cache-aside cache coherent. A
reader that loaded the row *before* a write can `set` it *after* the write's eviction. That
stale copy then gets cached into the config download under the new `config_version`, where
the SDK's ETag pins it. `TestConfigDownloadAfterConcurrentKillSwitch` reproduces this.

The ordering is the whole mechanism:

- A reader takes the generation **before** touching Postgres and caches under that
  generation. Bulk paths read it once, and prerequisite recursion receives it as
  `_generation`, so neither re-reads it per flag.
- A writer advances it **after** its rows commit and **before** bumping `config_version`.
- So **never invalidate inside `transaction.atomic()`**. Advancing before COMMIT lets a
  reader cache the soon-to-be-replaced rows under the new generation.
  `clear_foreign_variation_refs` collects what it touched and invalidates after its
  transaction for this reason.

A change to any flag invalidates every flag cached in that environment. That is deliberate:
writes are rare next to reads, and per-environment generations are what make a segment
fan-out cost one advance per environment.

## The generation and `config_version` move together, in that order

`FlagService._advance_and_bump` does both, and every invalidation path funnels through it,
`EnvironmentFlagService` included.

- **Never advance without bumping.** The version is the ETag for `GET /sdk/flags/config/`.
  An SDK polling a version that never moves while the server serves something new is worse
  than no versioning at all.
- **Never bump first.** A poll between the two would build the new version's payload from
  the old generation's entries.
- Bumps are an atomic `F()` update (`EnvironmentQuery.bump_config_versions`).
