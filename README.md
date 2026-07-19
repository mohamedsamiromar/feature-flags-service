# Feature Flag Engine

[![CI](https://github.com/mohamedsamiromar/feature-flags-service/actions/workflows/ci.yml/badge.svg)](https://github.com/mohamedsamiromar/feature-flags-service/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/mohamedsamiromar/feature-flags-service)](LICENSE)

A self-hosted feature flag backend built with Django and Django REST Framework. Redis-cached flag evaluation, rule-based user targeting, environment-scoped flag state, SDK key authentication, multivariate flags, and an audit trail.

Point an SDK key at `POST /api/v1/sdk/evaluate/` and evaluate flags over HTTP. Python 3.9, Django 4.2, PostgreSQL 15, Redis 7.

This is a personal project, not a hosted service. See [Known gaps](#known-gaps) before relying on it.

---

## Architecture

```text
┌─────────────────────────────────────────────────────────────────────┐
│                          REST API (DRF)                             │
│  JWT Auth (dashboard) · SDK Key Auth (SDK) · Versioned /api/v1/     │
└──────────┬──────────────────────────────┬───────────────────────────┘
           │                              │
  ┌────────▼────────┐             ┌───────▼──────────┐
  │  Dashboard API  │             │    SDK API       │
  │  Flag CRUD      │             │  POST /sdk/      │
  │  FlagService    │             │    evaluate/     │
  │  AuditService   │             │  SDKKeyAuth      │
  │  Environments   │             └───────┬──────────┘
  │  SDK Key Mgmt   │                     │
  └────────┬────────┘                     │
           └──────────────┬───────────────┘
                          │
           ┌──────────────▼──────────────┐
           │        Redis Cache          │
           │  flags:{owner}:{env}:{key}  │
           │  TTL: 300s (configurable)   │
           └──────────────┬──────────────┘
                          │ cache miss
                 ┌────────▼────────┐
                 │   PostgreSQL    │
                 │  flags · rules  │
                 │  environments   │
                 │  sdk_keys       │
                 │  audit · eval   │
                 └────────┬────────┘
                          │
                 ┌────────▼────────┐
                 │  Celery Worker  │
                 │  Async log write│
                 └─────────────────┘
```

### Evaluation algorithm

1. **Cache lookup** — resolve `flags:{owner_id}:{env_id}:{flag_key}` from Redis. On miss, query PostgreSQL and warm the cache.
2. **Kill switch** — if the environment's `is_enabled` is false, return `off_variation`.
3. **Targeting rules** — evaluate in `priority` order. First match serves the rule's `serve_variation` if set, otherwise `fallthrough_variation`.
4. **Percentage rollout** — `SHA-256(flag_key + user_id) % 100 < rollout_percentage`. Deterministic: the same user always lands in the same bucket. In the bucket serves `fallthrough_variation`, outside serves `off_variation`.

Every result carries a `result` value (boolean, string, number, or JSON object) and a `result_type`. Cache keys are scoped to `(owner_id, env_id, flag_key)`, so each environment has independent cached state.

---

## Features

### Core flag engine

- **Flag CRUD** — flags identified by a human-readable `key` (e.g. `dark-mode`).
- **Percentage rollout** — SHA-256 deterministic bucket assignment.
- **Rule-based targeting** — ordered rules with operators `eq`, `neq`, `contains`, `in`, `not_in`, `gt`, `lt`.
- **Redis caching** — flag config and rules cached per `(owner, environment, key)`, invalidated on flag mutations, variation mutations, rule mutations, and environment flag state changes.
- **One-call toggle** — `POST /flags/{key}/toggle/` with `{"environment": "production"}` flips that environment's kill switch, invalidates the cache, and writes an audit entry. The per-environment state is created on first toggle (off by default, so the first call turns the flag on).

### Multivariate flags

- **Flag types** — every flag is `boolean` (default) or `multivariate`. Boolean flags automatically get `true`/`false` variations on creation.
- **Variation model** — each variation has a `name`, a `value_type` (`boolean`, `string`, `number`, `json`), and a `value` stored as a `JSONField`.
- **Off / fallthrough wiring** — `off_variation` is served when a flag is disabled, `fallthrough_variation` when a user lands in the rollout bucket. Both set via `PATCH /api/v1/flags/{key}/`.
- **Rule-level targeting** — a rule can specify a `serve_variation` returned instead of the fallthrough when it matches.
- **Typed responses** — the evaluate endpoint returns `result` and `result_type` so clients know how to handle the payload.
- **Backwards compatible** — if no variation is configured, the engine falls back to `true`/`false` booleans.

### Flag lifecycle

- **Archive / soft-delete** — `POST /api/v1/flags/{key}/archive/` soft-deletes without destroying history. Archived flags are excluded from list responses unless you pass `?include_archived=true`.
- **Unarchive** — `POST /api/v1/flags/{key}/unarchive/` restores. Mutations on an archived flag return `409 Conflict`.
- **Evaluation guard** — archived flags return `404` from the SDK evaluate endpoint.
- **Audited** — archive and unarchive both write an `AuditLog` entry.

### Version history & rollback

- **Automatic snapshots** — every flag create and config update appends an immutable `FlagVersion` capturing the restorable config (`name`, `description`, `is_enabled`, `rollout_percentage`, `flag_type`, and the off/fallthrough variation references).
- **History** — `GET /api/v1/flags/{key}/versions/` lists versions newest-first; `GET /api/v1/flags/{key}/versions/{n}/` returns a single snapshot with who changed it and when.
- **One-click rollback** — `POST /api/v1/flags/{key}/versions/{n}/rollback/` restores that snapshot onto the live flag. Rollback is append-only: it writes a new `rollback` version (recording the `source_version_no`) rather than rewriting history, invalidates every environment's cache, and writes an `AuditLog` entry.
- **Safe restores** — variation references that no longer exist are dropped to `null` on rollback rather than left dangling; rolling back an archived flag returns `409 Conflict`.

### Multi-environment

- **Environment model** — named environments (`development`, `staging`, `production`) owned by a user. Deleting one cascades to its SDK keys and per-environment flag states.
- **Per-environment state** — `EnvironmentFlag` links a flag to an environment with independent `is_enabled` and `rollout_percentage`. Update via `PATCH /api/v1/environments/{id}/flags/{flag_id}/`.
- **Environment-scoped cache** — cache keys include `env_id`, so toggling a flag in staging does not invalidate the production cache.

### SDK keys

- **Opaque tokens** — server (`sdk_srv_`) and client (`sdk_cli_`) types, each scoped to one environment.
- **Hashed at rest** — the raw key is returned once on creation and never stored. Only a SHA-256 hash is persisted; the 16-char prefix is kept for display.
- **Rotation** — `POST /api/v1/sdk-keys/{id}/rotate/` revokes the old key and issues a replacement in one request.
- **Revocation** — `POST /api/v1/sdk-keys/{id}/revoke/` deactivates immediately. Double-revoke returns `409 Conflict`.
- **Last-used tracking** — `last_used_at` updated on every authenticated SDK request.

### Security & auth

- **JWT authentication** — Bearer token auth on dashboard endpoints. `POST /api/v1/auth/token/` to obtain, `/refresh/` to rotate.
- **SDK key authentication** — `SDKKeyAuthentication` validates the `X-SDK-Key` header against the stored hash. The environment is derived from the key, so callers never pass `env_id`.
- **Ownership scoping** — dashboard querysets are filtered to `request.user`, and services assert ownership before mutating.
- **Cross-user rule assignment prevention** — `RuleSerializer.validate_flag()` blocks attaching a rule to another user's flag.
- **Rate limiting** — the evaluate endpoint has a dedicated `ScopedRateThrottle` (default 1,000/min, configurable).

### Observability & audit

- **Audit trail** — every flag create/update/delete/archive/unarchive and every environment toggle writes an `AuditLog` row with `old_value`/`new_value` JSON snapshots via a central `AuditService`. Variation mutations are not currently audited.
- **Evaluation logging** — every SDK evaluation is written to `EvaluationLog` by a Celery task, so the HTTP response returns without waiting on the DB write.
- **Read-only audit API** — `GET /api/v1/audit/`.

### Infrastructure

- **Health check** — `GET /healthz/` probes PostgreSQL (`SELECT 1`) and Redis (sentinel write/read). Returns `200` or `503`, no auth required.
- **Environment-variable config** — secrets, DB credentials, Redis URLs, JWT lifetimes, and throttle rates all come from `.env`. `SECRET_KEY` is required and has no fallback.
- **Persistent DB connections** — `CONN_MAX_AGE=60` reuses connections across requests.
- **Compound indexes** — `(flag_id, priority)` on rules, `(flag_id, evaluated_at DESC)` on evaluation logs, `(entity_type, entity_id)` and `(user_id, created_at DESC)` on audit logs.

---

## API reference

Import the [Postman collection](feature_flags.postman_collection.json) to explore every endpoint with example bodies.

```text
POST   /api/v1/auth/token/                          Obtain access + refresh token
POST   /api/v1/auth/token/refresh/                  Rotate access token

GET    /api/v1/flags/                               List flags (add ?include_archived=true to include archived)
POST   /api/v1/flags/                               Create a flag
GET    /api/v1/flags/{key}/                         Retrieve a flag
PATCH  /api/v1/flags/{key}/                         Update a flag (409 if archived)
DELETE /api/v1/flags/{key}/                         Delete a flag
POST   /api/v1/flags/{key}/toggle/                  Flip one environment's kill switch
POST   /api/v1/flags/{key}/archive/                 Archive a flag
POST   /api/v1/flags/{key}/unarchive/               Unarchive a flag
GET    /api/v1/flags/{key}/variations/              List variations
POST   /api/v1/flags/{key}/variations/              Create a variation
PATCH  /api/v1/flags/{key}/variations/{id}/         Update a variation
DELETE /api/v1/flags/{key}/variations/{id}/         Delete a variation

GET    /api/v1/rules/                               List rules for your flags
POST   /api/v1/rules/                               Create a rule
GET    /api/v1/rules/{id}/                          Retrieve a rule
PATCH  /api/v1/rules/{id}/                          Update a rule
DELETE /api/v1/rules/{id}/                          Delete a rule

GET    /api/v1/environments/                        List environments
POST   /api/v1/environments/                        Create an environment
GET    /api/v1/environments/{id}/                   Retrieve an environment
DELETE /api/v1/environments/{id}/                   Delete (cascades keys + flag states)
GET    /api/v1/environments/{id}/flags/             List per-environment flag states
PATCH  /api/v1/environments/{id}/flags/{flag_id}/   Update flag state for this environment

POST   /api/v1/sdk-keys/                            Create (returns full key once)
GET    /api/v1/sdk-keys/                            List (prefix only)
GET    /api/v1/sdk-keys/{id}/                       Retrieve a key
POST   /api/v1/sdk-keys/{id}/revoke/                Deactivate a key
POST   /api/v1/sdk-keys/{id}/rotate/                Revoke + issue replacement

POST   /api/v1/sdk/evaluate/                        Evaluate a flag (SDK key auth)

GET    /api/v1/evaluation/logs/                     List past evaluation logs
GET    /api/v1/audit/                               List audit log entries
GET    /api/v1/audit/{id}/                          Retrieve a single audit entry

GET    /healthz/                                    Database + Redis liveness probe (no auth)
```

### SDK evaluate

Header: `X-SDK-Key: sdk_srv_<token>`

```json
{
  "flag_key": "button-theme",
  "user_context": { "user_id": "u_123", "country": "EG", "plan": "pro" }
}
```

Response — `result` carries the variation value directly, `result_type` is one of `boolean`, `string`, `number`, `json`:

```json
{
  "flag_key": "button-theme",
  "result": "#ff0000",
  "result_type": "string",
  "environment": "production"
}
```

---

## Quick start

**Prerequisites:** Docker and Docker Compose.

```bash
git clone https://github.com/mohamedsamiromar/feature-flags-service.git
cd feature-flags-service

cp .env.example .env
# Set a strong SECRET_KEY — the app will not start without one.
# All other defaults work for local dev.

docker compose up --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
```

The `migrate` step is required — `docker compose up` does not run migrations.

Compose publishes host ports **8000** (web), **5434** (PostgreSQL) and **6379** (Redis). If something already owns 6379 or 8000 locally, the stack will not start until you free the port or change the mapping.

| Service | Port | Description |
| --- | --- | --- |
| `web` | 8000 | Django API server |
| `db` | 5434 | PostgreSQL 15 |
| `redis` | 6379 | Redis 7 |
| `celery` | — | Async task worker |
| `celery-beat` | — | Periodic task scheduler |

### Running tests

```bash
docker compose run --rm web pytest
```

143 tests. The same command runs in CI on every push and PR to `main`.

---

## Project structure

```text
feature_flags/
├── apps/
│   ├── accounts/       Custom User model + JWT auth URLs
│   ├── audit/          AuditLog model, AuditService, read-only API
│   ├── core/           BaseModel, shared exceptions, /healthz view
│   ├── environment/    Environment + EnvironmentFlag models, per-env state API
│   ├── evaluation/     FlagEvaluationService, EvaluationLog, Celery task
│   ├── flags/          FeatureFlag model, FlagService, CRUD + archive API
│   ├── rules/          Rule model, CRUD API
│   ├── sdk/            SDK evaluate endpoint (X-SDK-Key auth)
│   ├── sdk_keys/       SDKKey model, KeyGenerator, management API
│   └── targeting/      Operator matching logic (RuleEvaluator)
├── config/             settings.py · urls.py · celery.py
├── conftest.py         Shared pytest factories and fixtures
├── docker-compose.yml
└── requirements.txt
```

---

## Known gaps

- **Compose stores no data.** Neither `db` nor `redis` declares a volume, so `docker compose down` destroys the database. Fine for local development; not usable as-is for a real deployment.
- **Flag keys are globally unique.** `FeatureFlag.key` carries a database-wide `UNIQUE` constraint, so if one user creates `dark-mode`, no other user can. The `unique_flag_per_owner` constraint intended to replace it is declared on the model but has never been migrated.
- **Model/migration drift.** `manage.py makemigrations` reports pending changes on `environment`, `flags`, `rules`, and `sdk_keys` that no migration covers.
- **No benchmarks.** Nothing in this repo measures throughput, latency, or cache hit rate. Any performance characteristics are unmeasured.
- **No OpenAPI schema.** Use the Postman collection.

---

## Design decisions

**Why SHA-256 for rollout bucketing?**
`SHA-256(flag_key + user_id) % 100` is deterministic, so the same user always lands in the same bucket for a given flag, and it is trivial to reimplement in any SDK language. LaunchDarkly uses MurmurHash3; SHA-256 is slower but available in every standard library.

**Why SHA-256 for SDK key storage?**
SDK keys are long-lived credentials, so storing raw values would turn any database breach into a full key compromise. Only the hash is persisted, and lookup is a single indexed query on the hash rather than an iteration over candidates. The stored 16-char prefix lets a user identify a key without exposing the secret.

**Why soft-delete (archive) instead of hard-delete?**
Hard-deleting a flag destroys its audit history, evaluation logs, and rule configuration. Archiving preserves all of it while removing the flag from evaluation and list responses.

**Why async evaluation logging?**
Writing an `EvaluationLog` row synchronously puts a DB write in the hot path of every flag check. Celery decouples the two: the HTTP response returns immediately and the write happens in a worker with retries. The trade-off is that a failed write is invisible to the caller.

**Why is the cache scoped to `(owner_id, env_id, flag_key)`?**
Scoping by environment means toggling a flag in staging does not evict production's cached copy, so staging activity cannot cause production cache misses.

**Why is the cache invalidated on rule changes too?**
The cached payload embeds the flag's rules, so a rule write makes it stale. Rule mutations therefore call `FlagService.invalidate_flag_caches()`, which evicts the flag's cached copy in every environment it has state in.

**Why are variation values stored in a JSONField?**
A variation must hold a boolean, string, number, or arbitrary JSON object. `JSONField` covers all four without a column per type, and `value_type` records which one is stored so clients can deserialise it.

**Why are boolean flags auto-wired on creation?**
`FlagService.create_flag()` gives every boolean flag `true`/`false` variations and wires them to `fallthrough_variation`/`off_variation`. Boolean flags work with zero configuration while the engine still runs one variation-based code path.

---

## Roadmap

Nothing below is built.

- **Targeting** — individual user targeting, reusable segments, prerequisite flags, rule-level rollout within a segment.
- **SDK infrastructure** — bulk flag download, impression batching, SSE streaming of flag updates.
- **Workflow** — stale flag detection, scheduled changes, webhooks, approval workflows.
- **Analytics** — impression aggregation, data export.
- **Experimentation** — A/B testing framework, statistical significance reporting.
- **Enterprise** — projects/organizations, RBAC, SSO and SCIM.

---

## License

[MIT](LICENSE) © Mohamed Samir
