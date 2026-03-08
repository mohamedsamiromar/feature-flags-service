# Feature Flag Engine

A production-grade feature flag backend built from scratch in Python, modelled after the architecture of LaunchDarkly. Designed for high-read-throughput flag evaluation with a Redis caching layer, async impression logging via Celery, rule-based user targeting, and a full audit trail.

This is a long-term portfolio project. The core engine is complete and production-hardened. Advanced features (environments, multivariate flags, SSE streaming, experimentation) are actively in progress — see the [Roadmap](#roadmap) section.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        REST API (DRF)                       │
│   JWT Auth · Rate Limiting · Versioned at /api/v1/          │
└────────────────────┬────────────────────────────────────────┘
                     │
        ┌────────────┴────────────┐
        │                         │
┌───────▼──────┐         ┌────────▼────────┐
│  Flag CRUD   │         │  Evaluation      │
│  FlagService │         │  Engine          │
│  AuditService│         │  FlagEvaluation  │
└───────┬──────┘         │  Service         │
        │                └────────┬─────────┘
        │                         │
        │          ┌──────────────▼──────────────┐
        │          │        Redis Cache            │
        │          │  flags:{owner_id}:{flag_key} │
        │          │  TTL: 300s (configurable)     │
        │          └──────────────┬───────────────┘
        │                         │ cache miss
        │                ┌────────▼────────┐
        └───────────────►│   PostgreSQL     │
                         │  flags · rules  │
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

1. **Cache lookup** — resolve `flags:{owner_id}:{flag_key}` from Redis. On miss, query PostgreSQL and warm the cache.
2. **Kill switch** — if `is_enabled = false`, return `false` immediately.
3. **Targeting rules** — evaluate rules in `priority` order. First match returns `true`.
4. **Percentage rollout** — compute `SHA-256(flag_key + user_id) % 100 < rollout_percentage`. Deterministic: the same user always lands in the same bucket.
5. **Default** — return `false`.

---

## Tech Stack

| Layer | Technology |
|---|---|
| API framework | Django 4.2 + Django REST Framework |
| Authentication | JWT via `djangorestframework-simplejwt` |
| Database | PostgreSQL 15 |
| Cache | Redis 7 (DB 1) |
| Task queue | Celery 5 + Redis broker (DB 0) |
| Containerisation | Docker + Docker Compose |

---

## Features Built

### Core Flag Engine
- **Flag CRUD** — create, update, delete, list flags. Flags identified by a human-readable `key` (e.g. `dark-mode`).
- **Boolean flag evaluation** — `POST /api/v1/evaluation/evaluate/` resolves a flag for a given user context in a single Redis round-trip on cache hit.
- **Percentage rollout** — SHA-256-based deterministic bucket assignment. Same user always gets the same result for a given flag.
- **Rule-based targeting** — ordered rules with operators: `eq`, `neq`, `contains`, `in`, `not_in`, `gt`, `lt`. Rules evaluated by priority.
- **Redis caching** — flag config and rules cached per owner+key. Cache is invalidated on every flag update and every rule mutation.

### Security & Auth
- **JWT authentication** — Bearer token auth on all endpoints. `POST /api/v1/auth/token/` to obtain, `POST /api/v1/auth/token/refresh/` to rotate.
- **Ownership isolation** — every query is scoped to `request.user`. No cross-user data leakage is possible.
- **Cross-user rule assignment prevention** — `RuleSerializer.validate_flag()` blocks attaching a rule to another user's flag at the serializer layer.
- **Rate limiting** — evaluation endpoint has a dedicated `ScopedRateThrottle` (default 1,000 req/min, configurable per environment).
- **Rollout percentage constraint** — enforced at three layers: serializer validator, Django model validator, and PostgreSQL `CheckConstraint`.

### Observability & Audit
- **Audit trail** — every flag create/update/delete writes an `AuditLog` row with `old_value` and `new_value` JSON snapshots via a centralized `AuditService`.
- **Evaluation logging** — every flag check is logged to `EvaluationLog` asynchronously via a Celery task. The HTTP response is returned before the DB write completes.
- **Read-only audit API** — `GET /api/v1/audit/` exposes the audit trail to the owning user.

### Infrastructure
- **Health check endpoint** — `GET /healthz/` probes PostgreSQL (`SELECT 1`) and Redis (sentinel write/read). Returns `200` or `503`. No auth required — safe for load balancers and k8s probes.
- **Environment-variable configuration** — all secrets, DB credentials, Redis URLs, JWT lifetimes, and throttle rates loaded from `.env`. No hardcoded values.
- **Persistent DB connections** — `CONN_MAX_AGE=60` reduces TCP handshake overhead at high throughput.
- **Compound DB indexes** — `(flag_id, priority)` on rules, `(flag_id, evaluated_at DESC)` on evaluation logs, `(entity_type, entity_id)` and `(user_id, created_at DESC)` on audit logs.

---

## API Reference

### Authentication
```
POST   /api/v1/auth/token/          Obtain access + refresh token
POST   /api/v1/auth/token/refresh/  Rotate access token
```

### Flags
```
GET    /api/v1/flags/               List all flags for the authenticated user
POST   /api/v1/flags/               Create a flag
GET    /api/v1/flags/{key}/         Retrieve a flag
PATCH  /api/v1/flags/{key}/         Update a flag
DELETE /api/v1/flags/{key}/         Delete a flag
```

### Rules
```
GET    /api/v1/rules/               List rules for the authenticated user's flags
POST   /api/v1/rules/               Create a rule
GET    /api/v1/rules/{id}/          Retrieve a rule
PATCH  /api/v1/rules/{id}/          Update a rule
DELETE /api/v1/rules/{id}/          Delete a rule
```

### Evaluation
```
POST   /api/v1/evaluation/evaluate/ Evaluate a flag for a user context
GET    /api/v1/evaluation/logs/     List past evaluation logs
```

Evaluation request body:
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

Evaluation response:
```json
{
  "flag_key": "dark-mode",
  "result": true
}
```

### Audit
```
GET    /api/v1/audit/               List audit log entries for the authenticated user
GET    /api/v1/audit/{id}/          Retrieve a single audit entry
```

### Infrastructure
```
GET    /healthz/                    Database + Redis liveness probe (no auth)
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
|---|---|---|
| `web` | 8000 | Django API server |
| `db` | 5432 | PostgreSQL 15 |
| `redis` | 6379 | Redis 7 |
| `celery` | — | Async task worker |
| `celery-beat` | — | Periodic task scheduler |

---

## Project Structure

```
feature_flags/
├── apps/
│   ├── accounts/       Custom User model + JWT auth URLs
│   ├── audit/          AuditLog model, AuditService, read-only API
│   ├── core/           BaseModel, custom exceptions, /healthz view
│   ├── evaluation/     Evaluation engine, EvaluationLog, Celery task
│   ├── flags/          FeatureFlag model, FlagService, CRUD API
│   ├── rules/          Rule model, RuleEvaluator, CRUD API
│   └── targeting/      Operator matching logic (RuleEvaluator)
├── config/
│   ├── settings.py     All config via environment variables
│   ├── urls.py         Root URL config
│   └── celery.py       Celery app configuration
├── docker-compose.yml
├── .env.example        Environment variable template
└── requirements.txt
```

---

## Roadmap

### Phase 1 — Foundational Data Model
- [ ] Flag version history and one-click rollback
- [ ] Flag archive / soft-delete
- [ ] Dedicated flag toggle endpoint (`POST /flags/{key}/toggle/`)
- [ ] Environments (production / staging / dev) — per-environment flag state and rules
- [ ] Projects and Organizations — team-level multi-tenancy
- [ ] SDK keys — long-lived tokens scoped to one environment
- [ ] Multivariate flags — string / number / JSON variations, not just booleans

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

**Why async evaluation logging?**
Writing an `EvaluationLog` row synchronously on every flag check adds DB write latency to the hot path. At 1,000+ evaluations/second this saturates the connection pool. Celery decouples the hot path from the write path — the HTTP response returns immediately and the log write happens asynchronously with automatic retry.

**Why is the cache invalidated on rule changes too?**
The Redis cache stores the full flag config including its rules. If a rule is added, updated, or deleted, the cached config becomes stale. Both `FlagService` (flag mutations) and `RuleViewSet` (rule mutations) call `FlagService._invalidate_cache()` after every write. The next evaluation re-fetches from PostgreSQL and rewarms the cache.

**Three-layer rollout_percentage validation**
A value outside 0–100 causes silent misbehaviour: `percentage=150` enables every user because `hash % 100 < 150` is always true. The constraint is enforced at the DRF serializer (clean 400 response), the Django model validator (ORM-level), and a PostgreSQL `CheckConstraint` (database-level, bypassed-ORM-proof).
