# MEMORY.md — Feature Flag Engine

Living context for AI agents and contributors. Update this file when architectural decisions change, roadmap items complete, or important constraints are discovered.

---

## Project Identity

Portfolio-grade feature flag backend inspired by LaunchDarkly. Solo project by Mohamed Samir. Goal: demonstrate production engineering practices (caching, async writes, security, audit trail) in a real-world backend.

---

## Completed Features (as of 2026-05-25)

| ID | Feature | Notes |
|---|---|---|
| core | Flag CRUD + targeting rules | Operators: `eq`, `neq`, `contains`, `in`, `not_in`, `gt`, `lt` |
| core | Percentage rollout | SHA-256 bucketing — deterministic per `(flag_key, user_id)` |
| core | Redis caching | Per `(owner_id, env_id, flag_key)`, TTL 300s (configurable) |
| core | JWT auth + SDK key auth | Dashboard uses Bearer JWT; SDK uses `X-SDK-Key` header |
| core | Celery async evaluation logs | HTTP response decoupled from DB write |
| core | Audit trail | Every CRUD + archive action logged with old/new snapshots |
| core | Health check endpoint | `/healthz/` probes PostgreSQL + Redis, no auth |
| F-06 | Flag archive / soft-delete | Archived flags excluded from lists; `409` on mutation; `404` on SDK eval |
| env | Environment model | Named envs (production, staging, dev) per user |
| env | Per-environment flag state | `EnvironmentFlag` with independent `is_enabled` + `rollout_percentage` |
| env | Environment-scoped cache | Staging toggles do not invalidate production cache |
| F-03 | SDK key management | Server + client key types, SHA-256 storage, prefix display |
| F-03 | SDK key rotation | Atomic revoke + reissue in one request |
| F-03 | Rate limiting on SDK evaluate | `ScopedRateThrottle`, default 1,000 req/min |

---

## Active Roadmap (Next Up)

- Flag version history and one-click rollback (Phase 1)
- Dedicated toggle endpoint — `POST /flags/{key}/toggle/` (Phase 1)
- Multivariate flags — string / number / JSON variations (Phase 1)
- Individual user targeting — allowlist / denylist per flag (Phase 2)
- Reusable segments (Phase 2)
- Impression batching + bulk SDK download (Phase 3)
- SSE streaming for real-time SDK updates (Phase 3)
- Stale flag detection via Celery-beat (Phase 4)

---

## Key Architectural Decisions

### Cache Key Scope
Cache keys are `flags:{owner_id}:{env_id}:{flag_key}`. Including `env_id` means production and staging caches are fully independent — a staging deploy never degrades production cache hit rates.

### SDK Key Storage
Raw key is never stored. Only the SHA-256 hash is persisted. The 16-char prefix is stored for display. The full key is returned exactly once on creation. This means database compromise does not expose live credentials.

### Soft-Delete vs Hard-Delete
Archive preserves audit history, evaluation logs, and rule config. Hard-delete was rejected because it destroys forensic value. Unarchive is a zero-loss one-call restore.

### Async Evaluation Logging
`EvaluationLog` writes go through a Celery task. This keeps the SDK evaluate hot path free of DB write latency. At 1,000+ req/s, synchronous writes would saturate the connection pool.

### Three-Layer Validation
`rollout_percentage` (0–100) is enforced at: DRF serializer (400 response), Django model validator (ORM), PostgreSQL `CheckConstraint` (database-level, bypass-proof). Any new numeric invariant should use this pattern.

### Ownership Isolation
Every queryset is filtered by `owner=request.user`. This is the primary multi-tenancy boundary. No admin override bypasses this filter.

---

## Known Constraints and Gotchas

- `FlagService._invalidate_cache` uses an older cache key format `flags:{owner_id}:{flag_key}` (without `env_id`). `FlagEvaluationService.invalidate_cache` uses the correct `flags:{owner_id}:{env_id}:{flag_key}` format. Both must be called when mutating a flag at the flag level vs. environment level.
- `SDKKeyFactory._create` attaches `instance._full_key` to the instance for tests that authenticate with the key. This is a test-only attribute and does not exist on real `SDKKey` instances.
- `celerybeat-schedule` is a binary file produced by the Celery beat scheduler. It should not be committed — it is listed in `.gitignore`.
- `CONN_MAX_AGE=60` keeps PostgreSQL connections alive across requests. Set to `0` in tests if you see connection pool exhaustion.

---

## Infrastructure Topology

| Service | Port | Redis DB |
|---|---|---|
| Django web | 8000 | — |
| PostgreSQL | 5432 | — |
| Redis | 6379 | DB 0 = Celery broker, DB 1 = flag cache |
| Celery worker | — | — |
| Celery beat | — | — |

---

## Test Infrastructure

- Factories in `conftest.py` (project root): `UserFactory`, `FeatureFlagFactory`, `EnvironmentFactory`, `EnvironmentFlagFactory`, `SDKKeyFactory`
- `auth_client` fixture provides a pre-authenticated `APIClient`
- Tests hit real PostgreSQL — no ORM mocking
- `pytest.ini` configures `DJANGO_SETTINGS_MODULE=config.settings`
