# AGENTS.md — Feature Flag Engine

Instructions for AI agents (Claude Code, Copilot, Codex, etc.) working in this repository.

---

## Project Overview

A production-grade feature flag backend modelled after LaunchDarkly. Built with Django 4.2 + DRF, PostgreSQL, Redis, and Celery. Long-term portfolio project. The core engine, multi-tenancy, and the full targeting layer are complete; SDK infrastructure is next.

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
docker compose run --rm web pytest apps/flags/tests/ apps/segments/tests/ -v

# Create superuser (there is no self-serve registration endpoint yet)
docker compose exec web python manage.py createsuperuser

# Health check
curl http://localhost:8000/healthz/
```

Running the suite outside Docker needs Postgres and Redis reachable:

```bash
docker compose up -d db redis
DB_HOST=localhost DB_PORT=5434 REDIS_URL=redis://localhost:6379 pytest -q
```

---

## Architecture

```text
REST API (DRF)
  ├── Dashboard API  →  JWT Auth (Bearer token)
  └── SDK API        →  SDK Key Auth (X-SDK-Key header)
         │
    Redis Cache  (DB 1)  — cache key: flags:{project_id}:{env_id}:{flag_key}  TTL: 300s
         │ miss
    PostgreSQL
         │
    Celery Worker  (Redis DB 0)  — async evaluation log writes
```

### Tenancy

`Organization → Membership(user, role) → Project → {FeatureFlag, Segment, Environment}`

The **project** is the tenancy boundary, not the user. Roles are `viewer < member < admin < owner`. A project you are not a member of returns **404** (invisible); a member with too low a role gets **403**.

### Evaluation Algorithm (in order — each step short-circuits)

1. Redis cache lookup — `flags:{project_id}:{env_id}:{flag_key}`
2. **Kill switch** — `EnvironmentFlag.is_enabled` false → `off_variation`. Nothing overrides this.
3. **Prerequisites** — each gate's flag is evaluated recursively for the same user; any unmet gate → `off_variation`
4. **Individual targets** — `FlagTarget` on `user_context["user_id"]` → that variation
5. **Rules** in `priority` order — first match wins outright; its own `rollout_percentage` decides if this user is in the served slice, else `off_variation` (no fall-through to later rules)
6. **Percentage rollout** — `SHA-256(flag_key + user_id) % 100 < rollout_percentage`
7. Legacy fallback — a flag with no variations returns raw `true`/`false`

---

## App Layout

| App | Responsibility |
|---|---|
| `apps.accounts` | Custom `User` model, JWT auth URLs |
| `apps.organizations` | `Organization`, `Membership`, `Project`, `AccessService` (RBAC) |
| `apps.core` | `BaseModel`, `Error`/`APIError` catalogue, `/healthz/` |
| `apps.flags` | `FeatureFlag`, `Variation`, `FlagTarget`, `FlagPrerequisite`, `FlagVersion` |
| `apps.rules` | `Rule` model, targeting rule API |
| `apps.segments` | `Segment`, `SegmentTarget`, `SegmentRule`, `SegmentEvaluator` |
| `apps.targeting` | `RuleEvaluator` — operator matching logic |
| `apps.evaluation` | `FlagEvaluationService`, `EvaluationLog`, Celery task |
| `apps.audit` | `AuditLog` model, `AuditService`, read-only API |
| `apps.environment` | `Environment` + `EnvironmentFlag` models, per-env state API |
| `apps.sdk_keys` | `SDKKey` model, `KeyGenerator`, `SDKKeyAuthentication`, management API |
| `apps.sdk` | SDK evaluate endpoint (authenticated via `X-SDK-Key`) |

---

## Coding Conventions

### Four-layer architecture

Every app separates **view → serializer → service → query**:

- **View** — request/response only. No `if`, no `try`, no ORM.
- **Serializer** — (de)serialisation and field shape only. No DB, no cross-entity logic.
- **Service** (`*Service`) — all business logic, cache invalidation, audit. **No ORM.**
- **Query** (`queries.py`, `*Query` classes) — the ONLY place with ORM access.

```python
# Right — the service takes identifiers and fetches through the query layer
FlagService().create_flag(project_key=..., user=request.user, **validated_data)

# Wrong — ORM in a view
FeatureFlag.objects.create(project=..., **validated_data)

# Wrong — ORM in a service
Rule.objects.filter(flag=flag)          # belongs in RuleQuery
```

Cross-entity checks ("does this variation belong to this flag?") go in the **service**, never the serializer.

### Error handling

Add a new entry to the `Error` enum in `apps/core/errors.py` (unique negative code; **last used −417**) rather than a bespoke exception class. Services raise `APIError(Error.X, extra=[...])`; the global handler in `config/exception_handler.py` renders `{code, detail, alert}` with the declared status. Views need no `try/except`.

There is no `FlagArchivedError` or `DomainError` — the old `apps/core/exceptions.py` was deleted.

### Naming service lookup arguments

An `update_*` service method takes `<entity>_key`, **never** `key`. Views splat `**serializer.validated_data` alongside the lookup argument, so naming it after a writable serializer field is a `TypeError` (a 500), not a validation error. This was a real bug on `update_flag`.

### Audit logging

Mutations should call `AuditService.log(...)`, using `AuditService.snapshot(entity)` to capture `old_value` first. Django clears `instance.pk` on `.delete()`, so restore it before logging or the row records `entity_id="None"`:

```python
old_snapshot = AuditService.snapshot(obj)
Query.delete(obj)
obj.pk = old_snapshot["id"]          # required
AuditService.log(user=user, action=AuditService.DELETE, entity=obj,
                 old_value=old_snapshot, new_value=None)
```

Coverage today: flags, variations, environments, segments, targets, prerequisites. **Rules, SDK keys, and organizations are not yet audited** — worth closing.

### Cache invalidation

Call `FlagService.invalidate_flag_caches(flag)` after any mutation that changes what a flag serves — flag config, variations, rules, targets, prerequisites. For a mutation affecting many flags at once (segment edits), use `FlagService.invalidate_many_flag_caches(flags)`, which resolves every flag's environments in **one** query instead of one per flag.

Cache key format: `flags:{project_id}:{env_id}:{flag_key}` — one format, three call sites.

The cached payload embeds rules, targets, prerequisites, and the resolved segments those rules reference. Any stale entry causes incorrect evaluations.

### Membership scoping

Every queryset is scoped by project membership, and every mutation asserts role:

```python
project = ProjectQuery.get_for_member(project_key, user)   # 404 if not a member
AccessService.assert_can_write(user, project)              # 403 if role too low
```

Never read across projects, even in admin or debug paths.

### Validation layers

`rollout_percentage` (0–100) on both `FeatureFlag` and `Rule` is enforced at **three layers**: DRF serializer, Django model validator, PostgreSQL `CheckConstraint`. New numeric constraints follow the same pattern.

### Fail closed

Anything unresolvable must resolve to *off*, never *on*:

- An unknown segment reference never matches — **whatever the operator**. Inverting an unknown would make `not_in_segment` match everyone and turn a dangling reference into a full rollout.
- An unmet, unreachable, archived, or cyclic prerequisite leaves the dependent flag off.
- A missing key in a cached payload defaults to the permissive-for-that-field value that preserves prior behaviour (e.g. `rule.get("rollout_percentage", 100)`), because cache entries outlive a deploy by up to the TTL.

### Bucketing salts

Rule-level rollout is salted with the rule id so two rules at the same percentage pick different slices. The **flag-level** rollout must keep hashing exactly `f"{flag_key}{user_id}"` with no salt — that hash decides who already has a flag, so changing its inputs re-buckets every user and flips live flags on deploy.

### Celery boundary

`CELERY_TASK_SERIALIZER = "json"` and the cached flag config contains Python `set`s (segment membership). **Never pass `flag_data` or a segment payload to a Celery task** — pass evaluated results and scalars.

### Archived flags

Archived flags must not be mutable: call `FlagService._assert_active(flag)`, which raises `APIError(Error.FLAG_ARCHIVED)` (409). The SDK evaluate endpoint returns 404 for archived flags — never serve them.

---

## Testing Conventions

- All factories live in `conftest.py` at the project root.
- Use `factory-boy` factories (`UserFactory`, `FeatureFlagFactory`, `EnvironmentFactory`, `EnvironmentFlagFactory`, `VariationFactory`, `SDKKeyFactory`).
- `FeatureFlagFactory`/`EnvironmentFactory` accept an `owner=<user>` shim that auto-provisions a deterministic personal project; `personal_project_for(user)` returns it.
- Use the `auth_client` fixture for JWT calls and `api_client` for SDK-key calls — the SDK evaluate endpoint **rejects JWTs**.
- The `base` fixture gives the project-nested flag URL prefix.
- Tests hit a real database — **do not mock the ORM**.
- An autouse fixture clears the Redis cache around every test; do not remove it. Redis outlives the test database, so a recycled primary key can otherwise read a stale entry from an earlier run.

```python
def test_flag_create(auth_client, base):
    response = auth_client.post(f"{base}/", {"name": "My Flag", "key": "my-flag"}, format="json")
    assert response.status_code == 201
```

**Test through the layer you are claiming works.** Service-level tests do not prove an endpoint works — a required serializer field once made an entire feature unreachable over HTTP while every service test passed. Any new API surface needs at least one test through the serializer.

**Verify a test can fail.** After writing a test for a safety property, break the property and confirm *that* test fails. A test asserting two rollout slices differ once passed with the salt removed entirely, because the two rules were on different flags and the flag key separated them anyway.

---

## Key Invariants (Do Not Break)

1. The raw SDK key is **never stored** — only its SHA-256 hash. The full key is returned once on creation.
2. Archiving a flag invalidates its cache entry in all environments.
3. Rule, target, prerequisite, and segment mutations invalidate the affected flags' caches.
4. Evaluation logging is always async (Celery) — the HTTP response never waits on the DB write.
5. Cache keys are scoped to `(project_id, env_id, flag_key)` — environments are independent.
6. `rollout_percentage` is validated at all three layers.
7. Nothing overrides the kill switch. Prerequisites sit above individual targeting.
8. Uncertainty fails closed. Never invert an unresolvable reference.
9. A flag's `key` and a segment's `key` are immutable after creation — they are referenced by SDK calls, cache entries, version snapshots, and targeting rules.
10. Segments do not nest; `SegmentRule` forbids the segment operators.
11. Deleting a referenced segment (409) or a flag that gates another (409) is refused rather than left dangling.

---

## Environment Variables

All configuration comes from `.env` (see `.env.example`). No hardcoded secrets anywhere.

Key variables: `SECRET_KEY`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `REDIS_URL`, `FLAG_CACHE_TTL`, `THROTTLE_RATE_EVALUATION`.

---

## Roadmap Status

See `README.md` → Roadmap and `PROJECT_GUIDE.md` §8 for the full checklist.

**Complete:** Phase 1 (data model, multi-tenancy) and Phase 2 (targeting — individual targeting, segments, rule-level rollout, prerequisites).

**Active next items (Phase 3 — SDK infrastructure):**

- Impression batching endpoint (bulk eval-log ingest)
- Server-side SDK bulk download (`GET /sdk/flags/`)
- SSE streaming of flag updates

**Known gap worth closing first:** there is no `POST /api/v1/auth/register/`. It should also provision a personal organization and default project, mirroring `organizations/0002_backfill_personal_orgs`.
