# AGENTS.md — Feature Flag Engine

Instructions for AI agents (Claude Code, Copilot, Codex, etc.) working in this repository.

---

## Project Overview

A production-grade feature flag backend modelled after LaunchDarkly. Built with Django 4.2 + DRF, PostgreSQL, Redis, and Celery. The project is a long-term portfolio project; the core engine is complete, and advanced features are in active development.

---

## Essential Commands

```bash
# Start all services
docker compose up --build

# Apply migrations
docker compose exec web python manage.py migrate

# Run tests (all)
docker compose run --rm web pytest -v

# Run tests for specific apps
docker compose run --rm web pytest apps/flags/tests/ apps/sdk_keys/tests/ apps/sdk/tests/ -v

# Create superuser
docker compose exec web python manage.py createsuperuser

# Health check
curl http://localhost:8000/healthz/
```

---

## Architecture

```
REST API (DRF)
  ├── Dashboard API  →  JWT Auth (Bearer token)
  └── SDK API        →  SDK Key Auth (X-SDK-Key header)
         │
    Redis Cache  (DB 1)  — cache key: flags:{owner_id}:{env_id}:{flag_key}  TTL: 300s
         │ miss
    PostgreSQL
         │
    Celery Worker  (Redis DB 0)  — async evaluation log writes
```

### Evaluation Algorithm (in order)

1. Redis cache lookup — `flags:{owner_id}:{env_id}:{flag_key}`
2. Kill switch — if `EnvironmentFlag.is_enabled = False` → return `false`
3. Targeting rules — evaluated in `priority` order; first match → return `true`
4. Percentage rollout — `SHA-256(flag_key + user_id) % 100 < rollout_percentage`
5. Default → return `false`

---

## App Layout

| App | Responsibility |
|---|---|
| `apps.accounts` | Custom `User` model, JWT auth URLs |
| `apps.core` | `BaseModel`, shared exceptions, `/healthz/` |
| `apps.flags` | `FeatureFlag` model, `FlagService`, CRUD + archive endpoints |
| `apps.rules` | `Rule` model, CRUD endpoints |
| `apps.targeting` | `RuleEvaluator` — operator matching logic |
| `apps.evaluation` | `FlagEvaluationService`, `EvaluationLog`, Celery task |
| `apps.audit` | `AuditLog` model, `AuditService`, read-only API |
| `apps.environment` | `Environment` + `EnvironmentFlag` models, per-env state API |
| `apps.sdk_keys` | `SDKKey` model, `KeyGenerator`, `SDKKeyAuthentication`, management API |
| `apps.sdk` | SDK evaluate endpoint (authenticated via `X-SDK-Key`) |

---

## Coding Conventions

### Service Layer

Business logic lives in `*Service` classes, not in views or serializers. Views delegate all writes to the service.

```python
# Right
FlagService().create_flag(user, **validated_data)

# Wrong — don't do ORM writes in the view
FeatureFlag.objects.create(owner=request.user, **validated_data)
```

### Audit Logging

Every mutating operation (create / update / delete / archive / unarchive) **must** call `AuditService.log(...)`. Use `AuditService.snapshot(entity)` to capture `old_value` before the mutation.

### Cache Invalidation

Call `FlagEvaluationService.invalidate_cache(owner_id, flag_key, env_id)` after **every** flag or rule mutation, and after every `EnvironmentFlag` state change. The cache stores the full flag config including its rules — any stale entry causes incorrect evaluations.

Cache key format: `flags:{owner_id}:{env_id}:{flag_key}`

### Ownership Isolation

Every queryset must be scoped to `request.user`. Never expose cross-user data. The pattern:

```python
FeatureFlag.objects.filter(owner=request.user, ...)
```

### Validation Layers

`rollout_percentage` (0–100) is enforced at **three layers**: DRF serializer, Django model validator, PostgreSQL `CheckConstraint`. New numeric constraints should follow the same three-layer pattern.

### Archived Flags

Archived flags must not be mutatable. Check `flag.is_archived` and raise `FlagArchivedError` before applying any update. The SDK evaluate endpoint returns `404` for archived flags — never serve them.

---

## Testing Conventions

- All factories are defined in `conftest.py` at the project root.
- Use `factory-boy` factories (`UserFactory`, `FeatureFlagFactory`, `EnvironmentFactory`, `EnvironmentFlagFactory`, `SDKKeyFactory`).
- Use `auth_client` fixture for authenticated DRF test calls.
- Tests hit a real database — **do not mock the ORM**.
- Use `db` fixture (or `transactional_db` where needed) to enable database access.

```python
def test_flag_create(auth_client, user):
    response = auth_client.post("/api/v1/flags/", {"name": "My Flag", "key": "my-flag"})
    assert response.status_code == 201
```

---

## Key Invariants (Do Not Break)

1. The raw SDK key is **never stored** — only its SHA-256 hash is persisted. The full key is returned once on creation only.
2. Archiving a flag must invalidate its cache entry in all environments.
3. Deleting a rule must invalidate the cache for the flag it belongs to.
4. Evaluation logging is always async (Celery) — the HTTP response must not wait for the DB write.
5. Cache keys are scoped to `(owner_id, env_id, flag_key)` — staging and production caches are independent.
6. `rollout_percentage` must always be validated at all three layers.

---

## Environment Variables

All configuration comes from `.env` (see `.env.example`). No hardcoded secrets anywhere.

Key variables: `SECRET_KEY`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `REDIS_URL`, `FLAG_CACHE_TTL`, `THROTTLE_RATE_EVALUATION`.

---

## Roadmap Status

See `README.md` → Roadmap section for the full checklist. Active next items:
- Flag version history and rollback
- Dedicated toggle endpoint (`POST /flags/{key}/toggle/`)
- Multivariate flags (string / number / JSON variations)
- Individual user targeting (allowlist / denylist)
