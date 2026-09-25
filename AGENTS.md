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

# Create superuser (for /admin/; regular accounts come from POST /api/v1/auth/register/)
docker compose exec web python manage.py createsuperuser

# Health check
curl http://localhost:8000/healthz/
```

Running the suite outside Docker needs Postgres and Redis reachable:

```bash
docker compose up -d db redis
# Compose's Redis requires REDIS_PASSWORD (from .env)
export $(grep '^REDIS_PASSWORD=' .env)
DB_HOST=localhost DB_PORT=5434 REDIS_URL=redis://:$REDIS_PASSWORD@localhost:6379 pytest -q
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
| `apps.organizations` | `Organization`, `Membership`, `Invitation`, `Project`, `AccessService` (RBAC) |
| `apps.core` | `BaseModel`, `Error`/`APIError` catalogue, `/healthz/` |
| `apps.flags` | `FeatureFlag`, `Variation`, `FlagTarget`, `FlagPrerequisite`, `FlagVersion` |
| `apps.rules` | `Rule` model, targeting rule API |
| `apps.segments` | `Segment`, `SegmentTarget`, `SegmentRule`, `SegmentEvaluator` |
| `apps.targeting` | `RuleEvaluator` — operator matching logic |
| `apps.evaluation` | `FlagEvaluationService`, `EvaluationLog`, Celery task |
| `apps.audit` | `AuditLog` model, `AuditService`, read-only API |
| `apps.environment` | `Environment` + `EnvironmentFlag` models, per-env state API |
| `apps.sdk_keys` | `SDKKey` model, `KeyGenerator`, `SDKKeyAuthentication`, management API |
| `apps.sdk` | SDK endpoints (authenticated via `X-SDK-Key`): per-flag evaluate and bulk download |

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

Add a new entry to the `Error` enum in `apps/core/errors.py` (unique negative code; **last used −420**) rather than a bespoke exception class. Services raise `APIError(Error.X, extra=[...])`; the global handler in `config/exception_handler.py` renders `{code, detail, alert}` with the declared status. Views need no `try/except`.

There is no `FlagArchivedError` or `DomainError` — the old `apps/core/exceptions.py` was deleted.

### Naming service lookup arguments

An `update_*` service method takes `<entity>_key`, **never** `key`. Views splat `**serializer.validated_data` alongside the lookup argument, so naming it after a writable serializer field is a `TypeError` (a 500), not a validation error. This was a real bug on `update_flag`.

### Audit logging

Mutations call `AuditService.log(...)`, using `AuditService.snapshot(entity)` to capture `old_value` first. **Deletes go through `AuditService.log_delete(...)`, never `log(...)`** — Django clears `instance.pk` on `.delete()`, so a plain `log` records `entity_id="None"` and detaches the entry from the row it describes. The helper restores the pk from the snapshot:

```python
old_snapshot = AuditService.snapshot(obj)
Query.delete(obj)
AuditService.log_delete(user=user, entity=obj, old_value=old_snapshot)
```

Only successful mutations are audited. A write rejected with a 400/409 changed nothing, and an entry for it makes the trail lie.

**`AuditService.REDACTED_FIELDS` strips secrets from snapshots**, keyed by `Model._meta.model_name` — today `SDKKey.hashed_key`. A registry rather than a per-call argument, because an argument is something a future caller forgets, and forgetting writes the secret into a second table with different access rules. Add to it whenever a model gains a credential-shaped field.

Coverage is complete: flags, variations, environments, segments, targets, prerequisites, rules, SDK keys, organizations, memberships, invitations, and projects. Signup provisions its org/project/environments through the query layer and audits them explicitly — keep it that way, an audit invariant with one exception is the one found during an incident.

### Cache invalidation

Call `FlagService.invalidate_flag_caches(flag)` after any mutation that changes what a flag serves — flag config, variations, rules, targets, prerequisites. For a mutation affecting many flags at once (segment edits), use `FlagService.invalidate_many_flag_caches(flags)`, which resolves every flag's environments in **one** query instead of one per flag.

Cache key format: `flags:{project_id}:{env_id}:{flag_key}` — written in exactly one place, `FlagEvaluationService._cache_key`. Never format it by hand; `EnvironmentFlagService` used to, and a second copy of that string is how a pre-tenancy version of this code evicted one key while evaluation read another.

The cached payload embeds rules, targets, prerequisites, and the resolved segments those rules reference. Any stale entry causes incorrect evaluations.

**Eviction and `Environment.config_version` always move together.** `FlagService._invalidate_env_caches` does both, and every eviction path funnels through it. Do not evict without bumping: the version is the ETag for `GET /sdk/flags/config/`, so an SDK polling a version that never moves while the server serves something new is worse than no versioning at all. `_evict_env_caches` is the eviction-only half, and exists solely so a segment fan-out bumps once for the whole set rather than once per flag.

### SDK config download and conformance vectors

`GET /sdk/flags/config/` ships the raw ruleset, so every server-side SDK reimplements the engine. `sdk_conformance_vectors.json` is what stops them diverging, and CI checks it is current.

**Any change to evaluation behaviour changes the vectors.** That is the design. Run `python manage.py generate_conformance_vectors` and review the diff — it is the reviewable record that an SDK contract moved. Never edit the file by hand; it is generated by running the engine over the fixture in `apps/evaluation/conformance.py`.

Every pk in that fixture is explicit, in reserved bands. Rule-level bucketing is salted with the rule id, so ids from a database sequence would make the vectors differ per machine and the CI check could never pass.

`config_for` builds its payload from `_preload_flag_data` — the same map `evaluate_all` uses — not from a query of its own. Do not "optimise" that into a dedicated query: the point is that local and server evaluation read literally the same dicts.

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

`CELERY_TASK_SERIALIZER = "json"` and the cached flag config contains Python `set`s (segment membership). **Never pass `flag_data` or a segment payload to a Celery task** — pass evaluated results and scalars. `log_evaluations` (the ingest primitive behind `POST /sdk/impressions/`) takes `[{flag_id, result, context_data}, ...]`; keep that list to scalars and JSON values. The client bootstrap endpoint dispatches **no** task at all — see below.

**Changing a task signature needs the worker restarted, and in a real deploy, ordered.** Celery does not auto-reload, so `docker compose restart celery` after editing `tasks.py` — otherwise the web container queues the new shape and the old worker rejects it (`Reject: missing 1 required keyword-only argument`), silently dropping every message in between. This bit during the impression-batching work: the endpoint returned `202`, and nothing was written. Deploy workers before web, or make the new argument optional for one release.

### Bulk evaluation

`FlagEvaluationService.evaluate_all` (behind `POST /sdk/flags/evaluate/`) resolves an entire environment in a fixed number of round trips. Three things keep it that way — each has a test that fails if you remove it:

- **`_get_flag_data` consults the `preloaded` map before Redis.** `evaluate_all` resolves every flag's payload once and passes it down through `evaluate(_preloaded=...)`, prerequisite recursion included. Without it, one bulk call is N Redis reads.
- **`_build_rules` uses `sorted(flag.rules.all(), ...)`, never `.order_by()`.** `order_by` builds a new queryset and discards the `rules__serve_variation` prefetch, costing a query for the rules plus one per rule — on *both* the single and bulk paths.
- **Segments for all cache misses resolve in ONE `SegmentQuery.evaluation_payload` call**, then get sliced per flag. Each cached entry still carries only the segments its own rules name, so a bulk warm writes the same payload a single evaluation would.

Both paths share `_build_flag_data`, so they can never write differently shaped cache entries. If you add a field to the cached payload, add it there and nowhere else.

The one query a warm bulk call cannot avoid is `EvaluationQuery.active_flag_keys` — a warm cache knows each flag's config, not which flags exist. Do not "fix" that by caching the key index: a stale index makes a newly created flag invisible for the full TTL.

### Numeric operators coerce, and fail closed when they cannot

`gt` / `lt` run through `RuleEvaluator._evaluate_numeric`, which coerces both sides with `rules.models.to_number` and returns False if either will not convert. Until 2026-08-29 this was a bare `float(user_value)`: a rule like `age gt 18` against `{"age": "unknown"}` raised `ValueError`, uncaught, → **500 on `POST /sdk/evaluate/`**.

The user context is arbitrary runtime input from the caller's own application, so it can never be validated ahead of time — the only options were fail closed or return an error from the customer's hot path. Fail closed matches every other unresolvable case in the engine.

**Neither operator may be written as the negation of the other.** Both return False on junk; `not gt` would match every user whose attribute is unusable.

Authored rule `value`s are checked separately, at write time — `RuleService._assert_numeric_value` and `SegmentService._assert_numeric_value` reject a non-numeric value for `gt`/`lt` with `NON_NUMERIC_COMPARISON` (-418), because a rule that fails closed forever with nothing to show why is a silent misconfiguration. The check reads the rule as it will be *after* the write, so changing only the operator cannot slip past it.

Do not write new operators that coerce types without deciding what a failed coercion means; the engine's answer everywhere else is "does not match".

### Impressions are reads, not downloads

`POST /sdk/impressions/` is where locally-evaluated flags reach `EvaluationLog`. Its `user_context` is per impression, never per batch — a server SDK's flush spans many users, and a shared context would force one request each. Unknown flag keys are dropped and named in `dropped`; never make one stale key reject a batch, or an SDK holding a slightly old config can never flush again.


`POST /sdk/flags/evaluate/` resolves every flag in an environment and logs **none** of them. A bootstrap is a download; the app may go on to read three of fifty, and writing all fifty to `EvaluationLog` — which has no rollup — records fetches nobody consumed. `POST /sdk/evaluate/` still logs, because it genuinely serves one flag to one caller.

Do not "restore" logging to the bootstrap endpoint. Impressions for those flags belong to `POST /sdk/impressions/`, where the SDK reports what it actually used. `TestBulkImpressionLogging` fails if a task is dispatched or a row is written.

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
8. Uncertainty fails closed. Never invert an unresolvable reference — that covers an unknown segment key, a missing prerequisite, and an operand `gt`/`lt` cannot coerce.
9. A flag's `key` and a segment's `key` are immutable after creation — they are referenced by SDK calls, cache entries, version snapshots, and targeting rules.
10. Segments do not nest; `SegmentRule` forbids the segment operators.
11. Deleting a referenced segment (409) or a flag that gates another (409) is refused rather than left dangling.
12. Bulk evaluation is the same engine as per-flag evaluation, not a second implementation — it calls `evaluate` and shares `_build_flag_data`.
13. Every writable foreign key is ownership-checked in the service, on create **and** update. A `ModelSerializer` resolves a pk against the whole table, across every tenant, and the engine serves a referenced variation's value verbatim — `off_variation`, `fallthrough_variation`, and `serve_variation` once let any account read another tenant's values through its own SDK key. A child's parent (`Rule.flag`) is immutable after creation: a move is authorized against the destination only, so it lets a viewer strip targeting from a flag they cannot edit.
14. Only an owner may grant `owner`, or change or remove an owner's membership (`MembershipService._assert_may_touch_owner_rank`). ADMIN manages members, not the rank above it: an admin who can promote themselves lifts the last-owner guard, then demotes the real owner and deletes the org.
15. Nobody joins an organization without consent. A `Membership` is created only by the invitee accepting an `Invitation` — there is no direct add. The inviter's authority is re-checked at accept time, so an invitation from someone who has since left or lost the rank it grants is void (409 `INVITATION_VOID`).

---

## Environment Variables

All configuration comes from `.env` (see `.env.example`). No hardcoded secrets anywhere.

Key variables: `SECRET_KEY`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `REDIS_URL`, `FLAG_CACHE_TTL`, `THROTTLE_RATE_EVALUATION`, `THROTTLE_RATE_EVALUATION_BULK`.

---

## Roadmap Status

See `README.md` → Roadmap and `PROJECT_GUIDE.md` §8 for the full checklist.

**Complete:** Phase 1 (data model, multi-tenancy) and Phase 2 (targeting — individual targeting, segments, rule-level rollout, prerequisites).

**Phase 3 (SDK infrastructure) — complete except SSE:**

- ✅ SDK client bootstrap — `POST /sdk/flags/evaluate/` (one user context, every flag)
- ✅ SDK config download — `GET /sdk/flags/config/` (raw ruleset for server-side SDKs). See `SDK_CONFIG_SPEC.md`
- ✅ Impression batching — `POST /sdk/impressions/`
- SSE streaming of flag updates — blocked on ASGI, not on features. Under WSGI each open stream holds a worker thread; `config_version` polling covers the gap.

**Phases 5–7 are deliberately out of scope, not a backlog.** Analytics rollups, experimentation with statistical significance, and SSO/SCIM are documented as decisions in `README.md` → Deliberately out of scope. Do not start one because it appears "missing" — read the reasoning there first, and if it changes, change the reasoning rather than quietly shipping half of it.

**The two bulk endpoints are not alternatives.** The bootstrap endpoint costs one round trip per *user context* — right for a browser SDK (one user per session), wrong for a server-side SDK evaluating thousands of users per process. That is what the config download is for.

**Signup provisions tenancy, not just a user.** `POST /api/v1/auth/register/` creates the personal organization, `Default` project, and three environments alongside the user, in one transaction — the same shape `organizations/0002_backfill_personal_orgs` gave existing users. Anything that adds a *required* piece of per-account setup belongs in `RegistrationService.register`, inside that transaction: an account missing one of these has no API path back to a working state.
