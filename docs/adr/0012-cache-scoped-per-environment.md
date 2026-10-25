# 0012 — The flag cache is scoped per environment

**Status:** Accepted

## Context

If production and staging shared cache entries, activity in staging would cause cache
misses in production.

## Decision

Every payload key includes the environment:
`flags:{project_id}:{env_id}:{generation}:{flag_key}` (the generation came later, in
[0013](0013-invalidation-by-generation.md)). The key is built in exactly one place,
`FlagEvaluationService._cache_key`.

## Consequences

- Toggling a flag in staging never evicts production's copy.
- Never format the key by hand. A second copy of that string is how a pre-tenancy version
  of this code evicted one key while evaluation read another.
