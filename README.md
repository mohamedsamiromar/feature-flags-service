# Feature Flag Engine

![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![Django](https://img.shields.io/badge/django-4.2-green)
![Tests](https://img.shields.io/badge/tests-136%20passing-brightgreen)
![License](https://img.shields.io/badge/license-MIT-lightgrey)
![CI](https://img.shields.io/badge/CI-GitHub%20Actions-coming%20soon-yellow)

A self-hosted, production-grade feature flag engine you can deploy with a single `docker compose up`. Built in Python and modelled after the architecture of LaunchDarkly — with Redis-cached flag evaluation, async impression logging, rule-based user targeting, environment-scoped flag state, SDK key authentication, and a full audit trail.

Designed to be used as a backend service by any application: point your SDK key at `POST /api/v1/sdk/evaluate/` and start shipping flags in minutes. Advanced features (SSE streaming, experimentation) are in progress — see the [Roadmap](#roadmap) section.

---

## Architecture Overview

```text
┌─────────────────────────────────────────────────────────────────────┐
│                          REST API (DRF)                             │
│  JWT Auth (dashboard) · SDK Key Auth (SDK) · Versioned /api/v1/     │
└──────────┬──────────────────────────────┬───────────────────────────┘
           │                              │
  ┌────────▼────────┐             ┌───────▼──────────┐
  │  Dashboard API  │             │    SDK API        │
  │  Flag CRUD      │             │  POST /sdk/       │
  │  FlagService    │             │    evaluate/      │
  │  AuditService   │             │  SDKKeyAuth       │
  │  Environments   │             └───────┬──────────┘
  │  SDK Key Mgmt   │                     │
  └────────┬────────┘                     │
           │                              │
           └──────────────┬───────────────┘
                          │
           ┌──────────────▼──────────────┐
           │        Redis Cache           │
           │  flags:{owner}:{env}:{key}  │
           │  TTL: 300s (configurable)    │
           └──────────────┬──────────────┘
                          │ cache miss
                 ┌────────▼────────┐
                 │   PostgreSQL     │
                 │  flags · rules  │
                 │  environments   │
                 │  sdk_keys       │
                 │  audit · eval   │
                 └────────┬────────┘
                          │
                 ┌────────▼────────┐
                 │  Celery Worker   │
                 │  Async log write │
                 │  Scheduled tasks │
                 └─────────────────┘
```

### Evaluation Algorithm

Every flag evaluation follows this exact sequence:

1. **Cache lookup** — resolve `flags:{owner_id}:{env_id}:{flag_key}` from Redis. On miss, query PostgreSQL and warm the cache.
2. **Kill switch** — if the environment-level `is_enabled = false`, return the `off_variation` value immediately.
3. **Targeting rules** — evaluate rules in `priority` order. First match serves the rule's `serve_variation` (if set) or the flag's `fallthrough_variation`.
4. **Percentage rollout** — compute `SHA-256(flag_key + user_id) % 100 < rollout_percentage`. Deterministic: the same user always lands in the same bucket. Users in the bucket receive `fallthrough_variation`; users outside receive `off_variation`.
5. **Default** — return `off_variation`.

Every result carries a `result` value (boolean, string, number, or JSON object) and a `result_type` string. Cache keys are scoped to `(owner_id, env_id, flag_key)` so each environment maintains an independent cached state.

---

## Tech Stack

| Layer | Technology |
| --- | --- |
| API framework | Django 4.2 + Django REST Framework |
| Authentication | JWT (`djangorestframework-simplejwt`) + SDK key (custom `BaseAuthentication`) |
| Database | PostgreSQL 15 |
| Cache | Redis 7 (DB 1) |
| Task queue | Celery 5 + Redis broker (DB 0) |
| Containerisation | Docker + Docker Compose |

---

## Features Built

### Core Flag Engine

- **Flag CRUD** — create, update, delete, list flags. Flags identified by a human-readable `key` (e.g. `dark-mode`).
- **Percentage rollout** — SHA-256-based deterministic bucket assignment. Same user always gets the same result for a given flag.
- **Rule-based targeting** — ordered rules with operators: `eq`, `neq`, `contains`, `in`, `not_in`, `gt`, `lt`. Rules evaluated by priority.
- **Redis caching** — flag config and rules cached per `(owner, environment, key)`. Invalidated on every flag update, rule mutation, and environment flag state change.

### Multivariate Flags (F-07)

- **Flag types** — every flag is either `boolean` (default) or `multivariate`. Boolean flags automatically receive `true` and `false` variations on creation.
- **Variation model** — each variation has a `name`, a `value_type` (`boolean`, `string`, `number`, `json`), and a `value` stored as a `JSONField`. A flag can carry any number of named variations.
- **Off / fallthrough wiring** — `off_variation` is served when a flag is disabled; `fallthrough_variation` is served when a user lands in the rollout bucket. Both are set via `PATCH /api/v1/flags/{key}/`.
- **Rule-level targeting** — each targeting rule can specify a `serve_variation` (variation ID). When the rule matches, that specific variation is returned instead of the fallthrough.
- **Typed evaluation response** — the SDK evaluate endpoint now returns `result` (the variation value) and `result_type` (`boolean` / `string` / `number` / `json`) so clients know how to handle the payload.
- **Backwards compatible** — existing boolean flags created before F-07 continue to work. If no variation is configured, the engine falls back to `true` / `false` booleans so nothing breaks.

### Flag Lifecycle (F-06)

- **Archive / soft-delete** — `POST /api/v1/flags/{id}/archive/` soft-deletes a flag without destroying history. Archived flags are excluded from list responses by default; pass `?include_archived=true` to surface them.
- **Unarchive** — `POST /api/v1/flags/{id}/unarchive/` restores a flag. All mutations on an archived flag return `409 Conflict` until it is unarchived.
- **Evaluation guard** — archived flags return `404` from the SDK evaluate endpoint so live traffic is never served a stale result.
- **Audit log** — every archive and unarchive action is recorded with an `AuditLog` entry.

### Multi-Environment Support

- **Environment model** — named environments (e.g. `production`, `staging`) owned by a user. Deleting an environment cascades to its SDK keys and per-environment flag states.
- **Per-environment flag state** — `EnvironmentFlag` links a `FeatureFlag` to an `Environment` with independent `is_enabled` and `rollout_percentage` values. Update state via `PATCH /api/v1/environments/{id}/flags/{flag_id}/`.
- **Environment-scoped cache** — cache keys include `env_id`; toggling a flag in staging never invalidates the production cache.

### SDK Keys (F-03)

- **Long-lived opaque tokens** — server (`sdk_srv_`) and client (`sdk_cli_`) key types, scoped to one environment.
- **Secure by design** — raw key returned exactly once on creation and never stored. Only a SHA-256 hash is persisted. The prefix (first 16 chars) is stored for display.
- **Key rotation** — `POST /api/v1/sdk-keys/{id}/rotate/` atomically revokes the old key and issues a replacement in a single request.
- **Revocation** — `POST /api/v1/sdk-keys/{id}/revoke/` deactivates a key immediately. Double-revoke returns `409 Conflict`.
- **Last-used tracking** — `last_used_at` is updated on every authenticated SDK request.
- **SDK evaluate endpoint** — `POST /api/v1/sdk/evaluate/` authenticates via `X-SDK-Key` header. The environment is derived from the key itself; callers never pass `env_id`.

### Security & Auth

- **JWT authentication** — Bearer token auth on all dashboard endpoints. `POST /api/v1/auth/token/` to obtain, `POST /api/v1/auth/token/refresh/` to rotate.
- **SDK key authentication** — custom `SDKKeyAuthentication` class validates the `X-SDK-Key` header by comparing its SHA-256 hash against the database. Only the SDK evaluate endpoint accepts this auth.
- **Ownership isolation** — every query is scoped to `request.user`. No cross-user data leakage is possible at any layer.
- **Cross-user rule assignment prevention** — `RuleSerializer.validate_flag()` blocks attaching a rule to another user's flag at the serializer layer.
- **Rate limiting** — SDK evaluate endpoint has a dedicated `ScopedRateThrottle` (default 1,000 req/min, configurable via env var).
- **Rollout percentage constraint** — enforced at three layers: serializer validator, Django model validator, and PostgreSQL `CheckConstraint`.

### Observability & Audit

- **Audit trail** — every flag create/update/delete/archive/unarchive writes an `AuditLog` row with `old_value` and `new_value` JSON snapshots via a centralised `AuditService`.
- **Evaluation logging** — every SDK flag check is logged to `EvaluationLog` asynchronously via a Celery task. The HTTP response is returned before the DB write completes.
- **Read-only audit API** — `GET /api/v1/audit/` exposes the audit trail to the owning user.

### Infrastructure & Ops

- **Health check endpoint** — `GET /healthz/` probes PostgreSQL (`SELECT 1`) and Redis (sentinel write/read). Returns `200` or `503`. No auth required — safe for load balancers and k8s probes.
- **Environment-variable configuration** — all secrets, DB credentials, Redis URLs, JWT lifetimes, and throttle rates loaded from `.env`. No hardcoded values.
- **Persistent DB connections** — `CONN_MAX_AGE=60` reduces TCP handshake overhead at high throughput.
- **Compound DB indexes** — `(flag_id, priority)` on rules, `(flag_id, evaluated_at DESC)` on evaluation logs, `(entity_type, entity_id)` and `(user_id, created_at DESC)` on audit logs.

---

## API Reference

A [Postman collection](feature_flags.postman_collection.json) is included in the repository — import it to explore every endpoint without writing a single line of code.

> **OpenAPI / Swagger UI** — interactive docs via `drf-spectacular` are planned. Until then, use the Postman collection or the reference below.

### Authentication

```text
POST   /api/v1/auth/token/                          Obtain access + refresh token
POST   /api/v1/auth/token/refresh/                  Rotate access token
```

### Flags

```text
GET    /api/v1/flags/                               List flags (excludes archived by default)
GET    /api/v1/flags/?include_archived=true         List flags including archived
POST   /api/v1/flags/                               Create a flag
GET    /api/v1/flags/{key}/                         Retrieve a flag
PATCH  /api/v1/flags/{key}/                         Update a flag (409 if archived)
DELETE /api/v1/flags/{key}/                         Delete a flag
POST   /api/v1/flags/{key}/archive/                 Archive a flag
POST   /api/v1/flags/{key}/unarchive/               Unarchive a flag
GET    /api/v1/flags/{key}/variations/              List variations
POST   /api/v1/flags/{key}/variations/              Create a variation
PATCH  /api/v1/flags/{key}/variations/{id}/         Update a variation
DELETE /api/v1/flags/{key}/variations/{id}/         Delete a variation
```

Create flag request body:

```json
{
  "name": "Button Theme",
  "key": "button-theme",
  "flag_type": "multivariate"
}
```

`flag_type` is `boolean` (default) or `multivariate`. Create variation request body:

```json
{
  "name": "red",
  "value_type": "string",
  "value": "#ff0000"
}
```

`value_type` choices: `boolean`, `string`, `number`, `json`.

### Rules

```text
GET    /api/v1/rules/                               List rules for the authenticated user's flags
POST   /api/v1/rules/                               Create a rule
GET    /api/v1/rules/{id}/                          Retrieve a rule
PATCH  /api/v1/rules/{id}/                          Update a rule
DELETE /api/v1/rules/{id}/                          Delete a rule
```

The optional `serve_variation` field (variation ID) controls which variation is returned when the rule matches. Set it to `null` for boolean flags (the engine returns `true` on match).

```json
{
  "flag": 1,
  "attribute": "plan",
  "operator": "eq",
  "value": "premium",
  "priority": 1,
  "serve_variation": 3
}
```

### Environments

```text
GET    /api/v1/environments/                        List environments
POST   /api/v1/environments/                        Create an environment
GET    /api/v1/environments/{id}/                   Retrieve an environment
DELETE /api/v1/environments/{id}/                   Delete (cascades keys + flag states)
GET    /api/v1/environments/{id}/flags/             List per-environment flag states
PATCH  /api/v1/environments/{id}/flags/{flag_id}/   Update flag state for this environment
```

### SDK Keys

```text
POST   /api/v1/sdk-keys/                            Create (returns full key once)
GET    /api/v1/sdk-keys/                            List (prefix only, never full key)
GET    /api/v1/sdk-keys/{id}/                       Retrieve a key
POST   /api/v1/sdk-keys/{id}/revoke/                Deactivate a key
POST   /api/v1/sdk-keys/{id}/rotate/                Revoke + issue replacement
```

SDK key create request body:

```json
{
  "name": "Production Server",
  "key_type": "server",
  "environment": 1
}
```

SDK key create response (full key shown once):

```json
{
  "id": 1,
  "name": "Production Server",
  "key_type": "server",
  "prefix": "sdk_srv_Xy3mN8pQ",
  "is_active": true,
  "key": "sdk_srv_Xy3mN8pQ..."
}
```

### SDK Evaluate

```text
POST   /api/v1/sdk/evaluate/                        Evaluate a flag (SDK key auth)
```

Header: `X-SDK-Key: sdk_srv_<token>`

Request body:

```json
{
  "flag_key": "dark-mode",
  "user_context": {
    "user_id": "u_123",
    "country": "EG",
    "plan": "pro"
  }
}
```

Response:

```json
{
  "flag_key": "dark-mode",
  "result": true,
  "result_type": "boolean",
  "environment": "production"
}
```

`result_type` is one of `boolean`, `string`, `number`, or `json`. For multivariate flags `result` carries the variation value directly — a string, number, or JSON object:

```json
{
  "flag_key": "button-theme",
  "result": "#ff0000",
  "result_type": "string",
  "environment": "production"
}
```

### Evaluation Logs & Audit

```text
GET    /api/v1/evaluation/logs/                     List past evaluation logs
GET    /api/v1/audit/                               List audit log entries
GET    /api/v1/audit/{id}/                          Retrieve a single audit entry
```

### Infrastructure

```text
GET    /healthz/                                    Database + Redis liveness probe (no auth)
```

---

## Quick Start

**Prerequisites:** Docker and Docker Compose installed.

```bash
# 1. Clone the repository
git clone https://github.com/mohamedsamir/feature_flags.git
cd feature_flags

# 2. Set up environment variables
cp .env.example .env
# Open .env and set a strong SECRET_KEY — all other defaults work for local dev

# 3. Build and start all services
docker compose up --build

# 4. Apply database migrations
docker compose exec web python manage.py migrate

# 5. Create a superuser
docker compose exec web python manage.py createsuperuser
```

Services started by Docker Compose:

| Service | Port | Description |
| --- | --- | --- |
| `web` | 8000 | Django API server |
| `db` | 5434 | PostgreSQL 15 |
| `redis` | 6379 | Redis 7 |
| `celery` | — | Async task worker |
| `celery-beat` | — | Periodic task scheduler |

### Explore the API

Import [`feature_flags.postman_collection.json`](feature_flags.postman_collection.json) into Postman to get a ready-made collection of every endpoint with example request bodies.

### Running Tests

```bash
docker compose run --rm web pytest apps/flags/tests/ apps/sdk_keys/tests/ apps/sdk/tests/ -v
```

---

## Project Structure

```text
feature_flags/
├── apps/
│   ├── accounts/       Custom User model + JWT auth URLs
│   ├── audit/          AuditLog model, AuditService, read-only API
│   ├── core/           BaseModel, shared exceptions, /healthz view
│   ├── environment/    Environment + EnvironmentFlag models, per-env state API
│   ├── evaluation/     FlagEvaluationService, EvaluationLog, Celery task
│   ├── flags/          FeatureFlag model, FlagService, CRUD + archive API
│   ├── rules/          Rule model, RuleEvaluator, CRUD API
│   ├── sdk/            SDK evaluate endpoint (X-SDK-Key auth)
│   ├── sdk_keys/       SDKKey model, KeyGenerator, management API
│   └── targeting/      Operator matching logic (RuleEvaluator)
├── config/
│   ├── settings.py     All config via environment variables
│   ├── urls.py         Root URL config
│   └── celery.py       Celery app configuration
├── conftest.py         Shared pytest factories and fixtures
├── pytest.ini          Test runner configuration
├── docker-compose.yml
├── .env.example        Environment variable template
└── requirements.txt
```

---

## Roadmap

### Phase 1 — Foundational Data Model

- [ ] Flag version history and one-click rollback
- [x] Flag archive / soft-delete
- [ ] Dedicated flag toggle endpoint (`POST /flags/{key}/toggle/`)
- [x] Environments (production / staging / dev) — per-environment flag state
- [ ] Projects and Organizations — team-level multi-tenancy
- [x] SDK keys — long-lived tokens scoped to one environment
- [x] Multivariate flags — string / number / JSON variations, not just booleans

### Phase 2 — Targeting Power

- [ ] Individual user targeting (allowlist / denylist per flag)
- [ ] Reusable segments — define a user group once, use across any flag
- [ ] Prerequisite flags — flag B only evaluates if flag A resolves to a specific variation
- [ ] Rule-level percentage rollout within a matched segment

### Phase 3 — Real-Time SDK Infrastructure

- [ ] Impression batching endpoint (bulk evaluation log ingest)
- [ ] Server-side SDK bulk download (`GET /sdk/flags/`)
- [ ] SSE streaming — push flag updates to connected SDKs in real time

### Phase 4 — Workflow & Governance

- [ ] Stale flag detection (Celery-beat job, configurable staleness threshold)
- [ ] Scheduled flag changes (enable a flag at a specific datetime)
- [ ] Webhook notifications on flag mutations
- [ ] Approval workflows for production environment flag changes

### Phase 5 — Observability & Analytics

- [ ] Impression aggregation (hourly rollup table + stats endpoint)
- [ ] Data export to S3 / BigQuery

### Phase 6 — Experimentation

- [ ] A/B testing framework — link flags to experiments and conversion metrics
- [ ] Statistical significance reporting (frequentist Z-test)

### Phase 7 — Enterprise

- [ ] Role-based access control (admin / writer / reader per project)
- [ ] SSO and SCIM provisioning

---

## Design Decisions

**Why SHA-256 for rollout bucketing?**
The rollout hash is `SHA-256(flag_key + user_id) % 100`. This is deterministic (same user always lands in the same bucket for the same flag), uniformly distributed, and easy to replicate in any SDK language. LaunchDarkly uses MurmurHash3; SHA-256 has equivalent distribution properties with broader language support.

**Why SHA-256 for SDK key storage?**
SDK keys are long-lived credentials. Storing the raw value would make every database breach a full key compromise. Only the SHA-256 hash is persisted; the raw key is returned once on creation and never stored. Lookup is a constant-time hash comparison — no iteration required. The stored 16-char prefix lets users identify a key in the dashboard without exposing the secret.

**Why soft-delete (archive) instead of hard-delete?**
Hard-deleting a flag destroys its audit history, evaluation logs, and rule configuration. Archiving preserves all of that while removing the flag from active evaluation and list responses. Unarchiving is a one-call restore with zero data loss.

**Why async evaluation logging?**
Writing an `EvaluationLog` row synchronously on every flag check adds DB write latency to the hot path. At 1,000+ evaluations/second this saturates the connection pool. Celery decouples the hot path from the write path — the HTTP response returns immediately and the log write happens asynchronously with automatic retry.

**Why is the cache scoped to `(owner_id, env_id, flag_key)`?**
Scoping by environment means toggling a flag in staging never invalidates the production cache — and vice versa. Each environment maintains a fully independent cached state, keeping production cache hit rates high during staging deployments.

**Why is the cache invalidated on rule changes too?**
The Redis cache stores the full flag config including its rules. If a rule is added, updated, or deleted, the cached config becomes stale. Both `FlagService` (flag mutations) and `RuleViewSet` (rule mutations) call `FlagService._invalidate_cache()` after every write. The next evaluation re-fetches from PostgreSQL and rewarms the cache.

**Why are variation values stored in a JSONField?**
A `Variation` needs to hold a boolean, a string, a number, or an arbitrary JSON object — four types in one column. A `JSONField` handles all of them without schema changes per type. The `value_type` column records which type is stored so clients know how to deserialise the value. Storing typed values as raw JSON is the same approach used by LaunchDarkly's variation system.

**Why are boolean flags auto-wired on creation?**
Creating a boolean flag via `FlagService.create_flag()` automatically creates `true` and `false` variations and sets them as `fallthrough_variation` and `off_variation`. This means boolean flags work out of the box with zero extra configuration — the same UX as pre-F-07 — while the evaluation engine always runs the same variation-based code path, keeping the logic simple and consistent.

**Three-layer rollout_percentage validation**
A value outside 0–100 causes silent misbehaviour: `percentage=150` enables every user because `hash % 100 < 150` is always true. The constraint is enforced at the DRF serializer (clean 400 response), the Django model validator (ORM-level), and a PostgreSQL `CheckConstraint` (database-level, bypassed-ORM-proof).
