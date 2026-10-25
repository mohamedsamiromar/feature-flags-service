# AGENTS.md — Feature Flag Engine

Core instructions for AI agents. Topic rules are in [.claude/rules/](.claude/rules/) and
decisions in [docs/adr/](docs/adr/). Before editing a path, read the rule file that names
it (table at the end).

## Overview

A production-grade feature flag backend modelled after LaunchDarkly. It is built with
Django 4.2 + DRF on Python 3.9, PostgreSQL 15, Redis 7 and Celery, as a long-term portfolio
project. The core engine, multi-tenancy, the full targeting layer and the SDK
infrastructure are complete, except SSE, which is blocked on ASGI. Phases 5–7 (analytics
rollups, experimentation, SSO/SCIM) are deliberately out of scope, not a backlog. Read
README → Deliberately out of scope before starting one. If the reasoning there changes,
change the reasoning rather than quietly shipping half of a phase.

## Essential commands

```bash
docker compose up --build                          # web, db, redis, celery, celery-beat
docker compose exec web python manage.py migrate   # `up` does not migrate
# `run --rm` works whether or not the web container is up; `exec web` does not
docker compose run --rm web pytest -q                # full suite, about a minute
docker compose run --rm web pytest apps/flags/ -q    # one app
# CI runs these two besides the tests
docker compose run --rm web python manage.py makemigrations --check --dry-run
docker compose run --rm web python manage.py generate_conformance_vectors --check
curl http://localhost:8000/healthz/                  # 200 = Postgres + Redis healthy
```

Outside Docker, **for humans only; agents use the Docker commands above**, because it
reads `.env`. It needs `docker compose up -d db redis`, and must not run at the same time
as a Docker run, because they share the test database:

```bash
export $(grep '^REDIS_PASSWORD=' .env)
DB_HOST=localhost DB_PORT=5434 REDIS_URL=redis://:$REDIS_PASSWORD@localhost:6380 venv/bin/pytest -q
```

Host ports, bound to loopback only: web 8000 (`WEB_PORT`), Postgres 5434, Redis 6380.
Redis DB 0 is the Celery broker and DB 1 the flag cache. `docker compose down` keeps the
named volumes, while `down -v` destroys the data. Regular accounts come from
`POST /api/v1/auth/register/`; `createsuperuser` is only for `/admin/`. Curl recipes are in
[docs/recipes.md](docs/recipes.md).

## Architecture

```text
REST API (DRF)
  ├── Dashboard API → JWT (Bearer)        └── SDK API → X-SDK-Key
         │
    Redis cache (DB 1)  flags:{project_id}:{env_id}:{generation}:{flag_key}  TTL 300s
         │ miss
    PostgreSQL
         │
    Celery worker (Redis DB 0) — async evaluation-log writes
```

Tenancy: `Organization → Membership(user, role) → Project → {FeatureFlag, Segment, Environment}`.
The **project** is the tenancy boundary. Roles go `viewer < member < admin < owner`.

| App | Responsibility |
| --- | --- |
| `apps.accounts` | Custom `User`, JWT auth URLs, registration |
| `apps.organizations` | `Organization`, `Membership`, `Invitation`, `Project`, `AccessService` (RBAC) |
| `apps.core` | `BaseModel`, `Error`/`APIError` catalogue, `/healthz/` |
| `apps.flags` | `FeatureFlag`, `Variation`, `FlagTarget`, `FlagPrerequisite`, `FlagVersion` |
| `apps.rules` | `Rule` model, `Operator`, targeting rule API |
| `apps.segments` | `Segment`, `SegmentTarget`, `SegmentRule`, `SegmentEvaluator` |
| `apps.targeting` | `RuleEvaluator`: operator matching |
| `apps.evaluation` | `FlagEvaluationService`, `EvaluationLog`, Celery tasks, conformance fixture |
| `apps.audit` | `AuditLog`, `AuditService`, read-only API |
| `apps.environment` | `Environment`, `EnvironmentFlag`, per-env state API, `SecureModeService` |
| `apps.sdk_keys` | `SDKKey`, `KeyGenerator`, `SDKKeyAuthentication`, management API |
| `apps.sdk` | `X-SDK-Key` endpoints: evaluate, bootstrap, config download, impressions |

## Four layers

Every app separates **view → serializer → service → query**:

- **View**: request/response only. No business logic and no ORM. Dispatching on the HTTP
  method or headers is fine.
- **Serializer**: (de)serialisation and field shape only. No DB access and no
  cross-entity logic.
- **Service** (`*Service`): all business logic, cache invalidation and audit. **No ORM.**
- **Query** (`queries.py`, `*Query`): the ONLY place with ORM access.

```python
FlagService().create_flag(project_key=..., user=request.user, **validated_data)  # right
FeatureFlag.objects.create(project=..., **validated_data)  # wrong: ORM in a view
Rule.objects.filter(flag=flag)                             # wrong in a service: belongs in RuleQuery
```

Cross-entity checks ("does this variation belong to this flag?") go in the service, never
in the serializer. An `update_*` service method takes `<entity>_key`, **never** `key`.
Views splat `**serializer.validated_data` next to the lookup argument, so a lookup named
after a writable field raises a `TypeError` (a 500) rather than a validation error.

**Errors.** Add an entry to the `Error` enum in `apps/core/errors.py`. Codes are unique:
take the next unused code in that file. Services raise `APIError(Error.X, extra=[...])`,
and `config/exception_handler.py` renders `{code, detail, alert}` with the declared
status, so views need no `try/except`.

**Archived flags** are immutable: call `FlagService._assert_active(flag)`, which raises
`FLAG_ARCHIVED` (409). SDK endpoints return 404 for them and never serve them.

## Key invariants (do not break)

1. The raw SDK key is never stored. Only its SHA-256 hash is kept, and the full key is returned once, on creation.
2. Archiving a flag invalidates its cache entries in all environments.
3. Rule, target, prerequisite and segment mutations invalidate the affected flags' caches.
4. Evaluation logging is always async (Celery). No HTTP response waits on that DB write.
5. Cache keys are scoped to `(project_id, env_id, generation, flag_key)`. Readers take the generation before touching Postgres; writers advance it after commit and before the `config_version` bump.
6. `rollout_percentage` is validated at the serializer, the model validator and a PostgreSQL `CheckConstraint`. New numeric constraints follow the same three-layer pattern.
7. Nothing overrides the kill switch. Prerequisites sit above individual targeting.
8. Uncertainty fails closed. Never invert an unresolvable reference: an unknown segment, a missing prerequisite, or a `gt`/`lt` operand that won't coerce.
9. Flag and segment `key`s are immutable after creation.
10. Segments do not nest: `SegmentRule` forbids the segment operators.
11. Deleting a referenced segment, or a flag that gates another, is refused with a 409 rather than left dangling.
12. Bulk evaluation is the same engine as per-flag evaluation: it calls `evaluate` and shares `_build_flag_data`.
13. Every writable foreign key is ownership-checked in the service, on create **and** update. A child's parent is immutable.
14. Only an owner may grant `owner`, or change or remove an owner's membership.
15. Nobody joins an organization without consent: a `Membership` comes only from an accepted `Invitation`, re-checked at accept time.
16. A client SDK key reaches only `client_side_available` flags, and a hidden flag is indistinguishable from a missing one. New flags start hidden.
17. In a secure-mode environment, a client key can't evaluate a user id without a valid HMAC from the customer's backend. The secret is never serialized or audited.

## Configuration

All configuration comes from `.env` (see `.env.example`), and there are no hardcoded
secrets. Key variables: `SECRET_KEY`, `DB_*`, `REDIS_PASSWORD`, `REDIS_URL`,
`FLAG_CACHE_TTL`, `THROTTLE_RATE_EVALUATION`, `THROTTLE_RATE_EVALUATION_BULK`.

## Where the details live

| Rule file | Read before touching |
| --- | --- |
| [evaluation.md](.claude/rules/evaluation.md) | the engine: algorithm order, bucketing salts, fail closed, bulk/preload, conformance vectors, `client_side_only` |
| [operators.md](.claude/rules/operators.md) | targeting operators, numeric coercion, write-time `NON_NUMERIC_COMPARISON` |
| [cache.md](.claude/rules/cache.md) | invalidation: generations, `_advance_and_bump`, never inside `atomic()` |
| [sdk.md](.claude/rules/sdk.md) | key types, secure mode, impressions vs bootstrap logging |
| [tenancy.md](.claude/rules/tenancy.md) | membership scoping, 404 vs 403, FK ownership, owner rank, invitations, signup |
| [audit.md](.claude/rules/audit.md) | `log` vs `log_delete`, `REDACTED_FIELDS` |
| [celery.md](.claude/rules/celery.md) | the JSON boundary, restarting the worker |
| [testing.md](.claude/rules/testing.md) | factories, real DB, testing through the right layer |
| [migrations.md](.claude/rules/migrations.md) | migration checks, data migrations |

Decisions and their trade-offs: [docs/adr/README.md](docs/adr/README.md). For the roadmap,
see README → Roadmap and PROJECT_GUIDE §8. The config-download contract is in
[SDK_CONFIG_SPEC.md](SDK_CONFIG_SPEC.md).
