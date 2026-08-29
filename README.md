# Feature Flag Engine

[![CI](https://github.com/mohamedsamiromar/feature-flags-service/actions/workflows/ci.yml/badge.svg)](https://github.com/mohamedsamiromar/feature-flags-service/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/mohamedsamiromar/feature-flags-service)](LICENSE)

A self-hosted feature flag backend built with Django and Django REST Framework. Redis-cached flag evaluation, team multi-tenancy with role-based access, individual user targeting, reusable segments, prerequisite flags, environment-scoped state, SDK key authentication, multivariate flags, and an audit trail.

Point an SDK key at `POST /api/v1/sdk/evaluate/` for one flag, or `POST /api/v1/sdk/flags/evaluate/` to pull a whole environment in one call. Python 3.9, Django 4.2, PostgreSQL 15, Redis 7.

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
  │  Orgs/Projects  │             │  POST /sdk/      │
  │  Flag CRUD      │             │    evaluate/     │
  │  Targets        │             │  POST /sdk/flags/│
  │  Segments       │             │    evaluate/     │
  │  SDK Key Mgmt   │             │  SDKKeyAuth      │
  │                 │             └───────┬──────────┘
  │                 │                     │
  └────────┬────────┘                     │
           └──────────────┬───────────────┘
                          │
           ┌──────────────▼──────────────┐
           │        Redis Cache          │
           │ flags:{project}:{env}:{key} │
           │  TTL: 300s (configurable)   │
           └──────────────┬──────────────┘
                          │ cache miss
                 ┌────────▼────────┐
                 │   PostgreSQL    │
                 │  orgs · projects│
                 │  flags · rules  │
                 │  segments       │
                 │  environments   │
                 │  audit · eval   │
                 └────────┬────────┘
                          │
                 ┌────────▼────────┐
                 │  Celery Worker  │
                 │  Async log write│
                 └─────────────────┘
```

### The tenancy model

```text
Organization ── Membership(user, role) ── owner | admin | member | viewer
     │
     └── Project ──┬── FeatureFlag ──┬── Variation
                   │                 ├── FlagTarget        (individual users)
                   │                 ├── Rule              (targeting rules)
                   │                 └── FlagPrerequisite  (gates)
                   ├── Segment ──────┬── SegmentTarget
                   │                 └── SegmentRule
                   └── Environment ──┬── EnvironmentFlag   (per-env state)
                                     └── SDKKey
```

A project is the tenancy boundary. Flag keys are unique per project, so two teams can both own a `dark-mode` flag. A project you are not a member of is invisible — it returns `404`, not `403`.

### Evaluation algorithm

Order matters, and each step short-circuits:

1. **Cache lookup** — resolve `flags:{project_id}:{env_id}:{flag_key}` from Redis. On miss, query PostgreSQL and warm the cache.
2. **Kill switch** — if the environment's `is_enabled` is false, serve `off_variation`. Nothing below can override this.
3. **Prerequisites** — every gate must be satisfied: each prerequisite flag must resolve, for this same user, to the required variation. Any unmet gate serves `off_variation`.
4. **Individual targets** — if this `user_id` is pinned to a variation on this flag, serve it.
5. **Targeting rules** — evaluated in `priority` order. The first matching rule wins outright. A rule may test attributes directly or segment membership (`in_segment` / `not_in_segment`), and may carry its own `rollout_percentage` — matched users outside that slice get `off_variation` rather than falling through to a later rule.
6. **Percentage rollout** — `SHA-256(flag_key + user_id) % 100 < rollout_percentage`. Deterministic: the same user always lands in the same bucket. In the bucket serves `fallthrough_variation`, outside serves `off_variation`.

Every result carries a `result` value (boolean, string, number, or JSON object) and a `result_type`.

**Uncertainty fails closed.** A prerequisite that cannot be resolved — archived, missing from this environment, or part of a cycle — leaves the dependent flag off. An unresolvable segment reference matches nobody, whichever operator names it.

---

## Features

### Organizations, projects & RBAC

- **Hierarchy** — organizations contain projects; projects contain flags, environments, and segments.
- **Roles** — `owner`, `admin`, `member`, `viewer`, ranked. Writes to flags, environments, rules, segments, and SDK keys require `member` or above; managing members and projects requires `admin`; deleting an organization requires `owner`.
- **Invisible, not forbidden** — a project you are not a member of returns `404`. A member without sufficient role gets `403`.
- **Last owner protection** — an organization must always keep at least one owner.

### Core flag engine

- **Flag CRUD** — flags identified by a human-readable `key` (e.g. `dark-mode`), unique per project. The key is fixed at creation: it addresses SDK calls, cache entries, and every version snapshot, so changing it would break live integrations.
- **Percentage rollout** — SHA-256 deterministic bucket assignment.
- **Rule-based targeting** — ordered rules with operators `eq`, `neq`, `contains`, `in`, `not_in`, `gt`, `lt`, `in_segment`, `not_in_segment`.
- **Redis caching** — flag config, rules, targets, segments, and prerequisites cached per `(project, environment, key)`, invalidated on every mutation that could change an answer.
- **One-call toggle** — `POST /flags/{key}/toggle/` with `{"environment": "production"}` flips that environment's kill switch, invalidates the cache, and writes an audit entry. The per-environment state is created on first toggle (off by default, so the first call turns the flag on).

### Individual user targeting

- **Pin a user to a variation** — `FlagTarget` overrides targeting rules and the percentage rollout for one named user. Targeting the `true`/fallthrough variation is an allowlist; the `false`/off variation is a denylist.
- **Not above the kill switch** — a flag that is off serves the off variation to everyone, targets included. That is what makes the kill switch safe to reach for during an incident.
- **Idempotent writes** — `PUT` returns `201` the first time a user is targeted and `200` when moving them to a different variation, so a dashboard can push desired state blindly.

### Reusable segments

- **Define a group once** — a segment names a set of users that many flags can target, instead of the same condition copy-pasted onto every flag.
- **Membership** — explicit includes and excludes, plus attribute rules. Resolution order is excluded → included → any rule matching. Exclusion wins over everything, which makes it a reliable way to carve one account out of an otherwise rule-defined group.
- **Empty means nobody** — an unconfigured segment matches no one, never everyone.
- **Referenced by rules** — a flag rule uses `in_segment` / `not_in_segment` with the segment key as its value. Segments do not nest.
- **Safe to change** — editing a segment evicts the cache of every flag referencing it, so a membership change takes effect immediately rather than after the TTL. Deleting a segment a rule still references returns `409`.

### Prerequisite flags

- **Gate a flag behind another** — a flag evaluates normally only while its prerequisite serves a required variation for the same user, expressing feature dependency without duplicating the upstream flag's targeting.
- **Compared by variation identity** — not by value, since two variations of a flag may carry the same value.
- **Cycles rejected** — a graph walk at write time refuses any gate that would close a loop and reports the path; evaluation additionally carries the resolution chain and a depth cap, so even a cycle written directly to the database fails closed instead of recursing.
- **Dependents protected** — deleting or archiving a flag that gates another returns `409`, since letting it through would silently switch every dependent off.

### Multivariate flags

- **Flag types** — every flag is `boolean` (default) or `multivariate`. Boolean flags automatically get `true`/`false` variations on creation.
- **Variation model** — each variation has a `name`, a `value_type` (`boolean`, `string`, `number`, `json`), and a `value` stored as a `JSONField`.
- **Off / fallthrough wiring** — `off_variation` is served when a flag is disabled, `fallthrough_variation` when a user lands in the rollout bucket.
- **Typed responses** — the evaluate endpoint returns `result` and `result_type` so clients know how to handle the payload.
- **Backwards compatible** — if no variation is configured, the engine falls back to `true`/`false` booleans.

### Flag lifecycle

- **Archive / soft-delete** — `POST /flags/{key}/archive/` soft-deletes without destroying history. Archived flags are excluded from list responses unless you pass `?include_archived=true`.
- **Unarchive** — `POST /flags/{key}/unarchive/` restores. Mutations on an archived flag return `409 Conflict`.
- **Evaluation guard** — archived flags return `404` from the SDK evaluate endpoint.

### Version history & rollback

- **Automatic snapshots** — every flag create and config update appends an immutable `FlagVersion` capturing the restorable config.
- **History** — `GET /flags/{key}/versions/` lists versions newest-first; `GET .../versions/{n}/` returns a single snapshot with who changed it and when.
- **One-click rollback** — `POST /flags/{key}/versions/{n}/rollback/` restores that snapshot onto the live flag. Rollback is append-only: it writes a new `rollback` version recording the `source_version_no` rather than rewriting history.
- **Safe restores** — variation references that no longer exist are dropped to `null` rather than left dangling; rolling back an archived flag returns `409`.

### Multi-environment

- **Environment model** — named environments (`development`, `staging`, `production`) belonging to a project. Deleting one cascades to its SDK keys and per-environment flag states.
- **Per-environment state** — `EnvironmentFlag` links a flag to an environment with independent `is_enabled` and `rollout_percentage`.
- **Environment-scoped cache** — cache keys include `env_id`, so toggling a flag in staging does not invalidate the production cache.

### SDK keys

- **Opaque tokens** — server (`sdk_srv_`) and client (`sdk_cli_`) types, each scoped to one environment.
- **Hashed at rest** — the raw key is returned once on creation and never stored. Only a SHA-256 hash is persisted; the 16-char prefix is kept for display.
- **The key is the principal** — SDK requests authenticate as the key itself, not as a user. The environment and project are derived from it, so callers never pass `env_id`.
- **Rotation** — `POST /sdk-keys/{id}/rotate/` revokes the old key and issues a replacement in one request.
- **Revocation** — `POST /sdk-keys/{id}/revoke/` deactivates immediately. Double-revoke returns `409`.
- **Last-used tracking** — `last_used_at` updated on every authenticated SDK request.

### Security & auth

- **JWT authentication** — Bearer token auth on dashboard endpoints. `POST /api/v1/auth/token/` to obtain, `/refresh/` to rotate.
- **SDK key authentication** — `SDKKeyAuthentication` validates the `X-SDK-Key` header against the stored hash.
- **Membership scoping** — every dashboard queryset is filtered by project membership, and services assert role before mutating.
- **Three-layer numeric validation** — `rollout_percentage` (flag and rule) is enforced at the serializer, the model validator, and a PostgreSQL `CheckConstraint`.
- **Rate limiting** — the evaluate endpoints have dedicated `ScopedRateThrottle` scopes: `evaluation` (default 1,000/min) for the per-flag endpoint and `evaluation_bulk` (default 120/min) for the bulk one, which resolves an entire environment per call. Both configurable.

### Observability & audit

- **Audit trail** — flag, variation, environment, segment, target, and prerequisite mutations write an `AuditLog` row with `old_value`/`new_value` JSON snapshots via a central `AuditService`. Rule, SDK key, and organization mutations are not yet audited.
- **Evaluation logging** — `POST /sdk/evaluate/` writes an `EvaluationLog` row through a Celery task, so the HTTP response returns without waiting on the DB write. The client bootstrap endpoint deliberately logs **nothing**: it resolves an entire environment, but a bootstrap is a download, not a read, and recording fifty impressions for an app that goes on to use three would inflate a table that has no rollup. Impressions for bootstrapped flags will arrive through the batching endpoint, where the SDK reports what it actually read.
- **Read-only audit API** — `GET /api/v1/audit/`.

### Infrastructure

- **Health check** — `GET /healthz/` probes PostgreSQL (`SELECT 1`) and Redis (sentinel write/read). Returns `200` or `503`, no auth required.
- **Environment-variable config** — secrets, DB credentials, Redis URLs, JWT lifetimes, and throttle rates all come from `.env`. `SECRET_KEY` is required and has no fallback.
- **Persistent DB connections** — `CONN_MAX_AGE=60` reuses connections across requests.
- **Compound indexes** — `(flag_id, priority)` on rules, `(flag_id, evaluated_at DESC)` on evaluation logs, `(entity_type, entity_id)` and `(user_id, created_at DESC)` on audit logs.

---

## API reference

Import the [Postman collection](feature_flags.postman_collection.json) to explore every endpoint with example bodies.

Flags, environments, and segments are addressed under their project.

```text
POST   /api/v1/auth/token/                           Obtain access + refresh token
POST   /api/v1/auth/token/refresh/                   Rotate access token

GET    /api/v1/organizations/                        List your organizations
POST   /api/v1/organizations/                        Create an organization
GET    /api/v1/organizations/{slug}/                 Retrieve an organization
DELETE /api/v1/organizations/{slug}/                 Delete (owner only)
GET    /api/v1/organizations/{slug}/members/         List members
POST   /api/v1/organizations/{slug}/members/         Add a member (admin+)
PATCH  /api/v1/organizations/{slug}/members/{user}/  Change a member's role (admin+)
DELETE /api/v1/organizations/{slug}/members/{user}/  Remove a member (admin+)

GET    /api/v1/projects/                             List projects you can see
POST   /api/v1/projects/                             Create a project (admin+)
GET    /api/v1/projects/{key}/                       Retrieve a project
DELETE /api/v1/projects/{key}/                       Delete a project (admin+)

GET    /api/v1/projects/{pk}/flags/                  List flags (?include_archived=true)
POST   /api/v1/projects/{pk}/flags/                  Create a flag
GET    /api/v1/projects/{pk}/flags/{key}/            Retrieve a flag
PATCH  /api/v1/projects/{pk}/flags/{key}/            Update a flag (409 if archived)
DELETE /api/v1/projects/{pk}/flags/{key}/            Delete a flag
POST   /api/v1/projects/{pk}/flags/{key}/toggle/     Flip one environment's kill switch
POST   /api/v1/projects/{pk}/flags/{key}/archive/    Archive a flag
POST   /api/v1/projects/{pk}/flags/{key}/unarchive/  Unarchive a flag

GET    .../flags/{key}/variations/                   List variations
POST   .../flags/{key}/variations/                   Create a variation
PATCH  .../flags/{key}/variations/{id}/              Update a variation
DELETE .../flags/{key}/variations/{id}/              Delete a variation

GET    .../flags/{key}/targets/                      List individual user targets
PUT    .../flags/{key}/targets/                      Pin a user to a variation (upsert)
DELETE .../flags/{key}/targets/{user_key}/           Remove a target

GET    .../flags/{key}/prerequisites/                List prerequisite gates
PUT    .../flags/{key}/prerequisites/                Add or update a gate (upsert)
DELETE .../flags/{key}/prerequisites/{flag_key}/     Remove a gate

GET    .../flags/{key}/versions/                     List versions (newest first)
GET    .../flags/{key}/versions/{n}/                 Retrieve one snapshot
POST   .../flags/{key}/versions/{n}/rollback/        Restore that snapshot

GET    /api/v1/projects/{pk}/segments/               List segments
POST   /api/v1/projects/{pk}/segments/               Create a segment
GET    /api/v1/projects/{pk}/segments/{key}/         Retrieve a segment
PATCH  /api/v1/projects/{pk}/segments/{key}/         Update name/description
DELETE /api/v1/projects/{pk}/segments/{key}/         Delete (409 if referenced)
GET    .../segments/{key}/targets/                   List named members
PUT    .../segments/{key}/targets/                   Include or exclude a user (upsert)
DELETE .../segments/{key}/targets/{user_key}/        Remove a named member
GET    .../segments/{key}/rules/                     List attribute rules
POST   .../segments/{key}/rules/                     Add an attribute rule
PATCH  .../segments/{key}/rules/{id}/                Update a rule
DELETE .../segments/{key}/rules/{id}/                Delete a rule

GET    /api/v1/rules/                                List targeting rules
POST   /api/v1/rules/                                Create a targeting rule
GET    /api/v1/rules/{id}/                           Retrieve a rule
PATCH  /api/v1/rules/{id}/                           Update a rule
DELETE /api/v1/rules/{id}/                           Delete a rule

GET    /api/v1/projects/{pk}/environments/           List environments
POST   /api/v1/projects/{pk}/environments/           Create an environment
GET    /api/v1/projects/{pk}/environments/{id}/      Retrieve an environment
DELETE /api/v1/projects/{pk}/environments/{id}/      Delete (cascades keys + states)
GET    .../environments/{id}/flags/                  List per-environment flag states
PATCH  .../environments/{id}/flags/{flag_id}/        Update flag state for this env

POST   /api/v1/sdk-keys/                             Create (returns full key once)
GET    /api/v1/sdk-keys/                             List (prefix only)
GET    /api/v1/sdk-keys/{id}/                        Retrieve a key
POST   /api/v1/sdk-keys/{id}/revoke/                 Deactivate a key
POST   /api/v1/sdk-keys/{id}/rotate/                 Revoke + issue replacement

POST   /api/v1/sdk/evaluate/                         Evaluate one flag (SDK key auth)
POST   /api/v1/sdk/flags/evaluate/                   Evaluate every flag in the environment

GET    /api/v1/evaluation/logs/                      List past evaluation logs
GET    /api/v1/audit/                                List audit log entries
GET    /api/v1/audit/{id}/                           Retrieve a single audit entry

GET    /healthz/                                     Database + Redis liveness probe (no auth)
```

### SDK evaluate

Header: `X-SDK-Key: sdk_srv_<token>`

```json
{
  "flag_key": "button-theme",
  "user_context": { "user_id": "u_123", "country": "EG", "plan": "pro" }
}
```

`user_id` is the identity used for individual targeting, segment membership, and rollout bucketing. Every other key is available to targeting rules as an attribute.

Response — `result` carries the variation value directly, `result_type` is one of `boolean`, `string`, `number`, `json`:

```json
{
  "flag_key": "button-theme",
  "result": "#ff0000",
  "result_type": "string",
  "environment": "production"
}
```

### SDK bulk download

`POST /api/v1/sdk/flags/evaluate/` resolves every flag in the key's environment
for one user context in a single call — what an SDK asks for when it starts a
session, instead of one request per flag.

Header: `X-SDK-Key: sdk_srv_<token>`

```json
{
  "user_context": { "user_id": "u_123", "country": "EG", "plan": "pro" }
}
```

Response — flags keyed by flag key, so an SDK looks one up by name rather than
scanning a list. `variation_id` is `null` for a flag with no variations
configured:

```json
{
  "environment": "production",
  "flags": {
    "button-theme": { "result": "#ff0000", "result_type": "string", "variation_id": 42 },
    "dark-mode":    { "result": true,      "result_type": "boolean", "variation_id": 17 },
    "new-checkout": { "result": false,     "result_type": "boolean", "variation_id": 18 }
  }
}
```

It is the same engine as the per-flag endpoint — kill switch, prerequisites,
individual targets, rules, and rollout all apply identically, and a test
parametrised over every targeting layer asserts the two agree flag for flag.

**Why POST for a read?** The user context is an arbitrary nested object.
Query-string encoding it is lossy for anything but flat strings, and it would
put user attributes into access logs and proxy caches.

**What it costs.** A fixed number of round trips, independent of how many flags
the environment holds:

| | Round trips |
|---|---|
| Flag-key index | 1 query, always — a warm cache knows each flag's config, but not which flags exist |
| Cached payloads | 1 Redis `get_many` |
| Cache misses | 1 query for those flags, 1 for the union of segments they reference, 1 `set_many` |
| Evaluation | 0 — payloads are resolved once and handed to each evaluation, prerequisite chains included |

Measured flat at 9 queries for 1, 2, 5, 20, and 50 flags on a cold cache, and
exactly 1 query on a warm one. Archived flags, and flags not configured in this
environment, are omitted rather than reported as errors.

### Targeting examples

Pin one user into a flag regardless of rules or rollout:

```http
PUT /api/v1/projects/web/flags/new-checkout/targets/
{ "user_key": "u_123", "variation": 42 }
```

Roll a flag out to 20% of a segment:

```http
POST /api/v1/rules/
{ "flag": 7, "operator": "in_segment", "value": "beta-testers",
  "priority": 1, "serve_variation": 42, "rollout_percentage": 20 }
```

Gate a flag behind another flag:

```http
PUT /api/v1/projects/web/flags/new-checkout/prerequisites/
{ "prerequisite_key": "new-cart", "variation_id": 17 }
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

There is no self-serve signup endpoint yet, so the first user comes from `createsuperuser`. Existing users are given a personal organization and a default project by migration, and new organizations are created through the API.

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

359 tests. The same command runs in CI on every push and PR to `main`.

---

## Project structure

```text
feature_flags/
├── apps/
│   ├── accounts/       Custom User model + JWT auth URLs
│   ├── audit/          AuditLog model, AuditService, read-only API
│   ├── core/           BaseModel, error catalogue, /healthz view
│   ├── environment/    Environment + EnvironmentFlag models, per-env state API
│   ├── evaluation/     FlagEvaluationService, EvaluationLog, Celery task
│   ├── flags/          FeatureFlag, Variation, FlagTarget, FlagPrerequisite, FlagVersion
│   ├── organizations/  Organization, Membership, Project, AccessService (RBAC)
│   ├── rules/          Rule model + targeting rule API
│   ├── sdk/            SDK evaluate endpoint (X-SDK-Key auth)
│   ├── sdk_keys/       SDKKey model, KeyGenerator, management API
│   ├── segments/       Segment, SegmentTarget, SegmentRule, SegmentEvaluator
│   └── targeting/      Operator matching logic (RuleEvaluator)
├── config/             settings.py · urls.py · celery.py · exception_handler.py
├── conftest.py         Shared pytest factories and fixtures
├── docker-compose.yml
└── requirements.txt
```

Every app follows the same four layers: **view** (HTTP only), **serializer** (field shape only), **service** (`*Service`, business logic), and **query** (`queries.py`, the only place with ORM access).

---

## Known gaps

- **No self-serve registration.** There is no `POST /api/v1/auth/register/`; users are created via `createsuperuser` or the admin.
- **Compose stores no data.** Neither `db` nor `redis` declares a volume, so `docker compose down` destroys the database. Fine for local development; not usable as-is for a real deployment.
- **Partial audit coverage.** Flag, variation, environment, segment, target, and prerequisite mutations are audited. Rule, SDK key, and organization mutations are not.
- **Model/migration drift.** `manage.py makemigrations --check` still reports pending `Alter field id` changes on `evaluation` and `sdk_keys`.
- **Bootstrapped flags produce no impression data.** `POST /sdk/flags/evaluate/` writes nothing to `EvaluationLog` by design, and the batching endpoint that would carry those impressions is not built yet. Until it is, flags served through the bootstrap are invisible to `GET /api/v1/evaluation/logs/`.
- **Bulk download is evaluated, not raw config.** `POST /sdk/flags/evaluate/` returns resolved values for one user context, so an SDK cannot evaluate locally, work offline, or re-resolve a changed context without another request.
- **Prerequisite chains cost a cache read each on the per-flag endpoint.** `POST /sdk/evaluate/` resolves one cached entry per flag in the chain. No DB queries, but not free for deep chains. The bulk endpoint does not pay this — its preloaded payloads cover the whole environment, gate flags included.
- **`gt` / `lt` crash on a non-numeric attribute.** `RuleEvaluator._evaluate` calls `float(user_value)` unguarded, so a rule like `age gt 18` against a context of `{"age": "unknown"}` raises `ValueError` and returns **500** from `POST /sdk/evaluate/`. Pre-existing. Needs a decision — failing closed (no match) would match how every other unresolvable case in the engine behaves. Blocks the config-download spec ([SDK_CONFIG_SPEC.md](SDK_CONFIG_SPEC.md) §9.1).
- **No benchmarks.** Nothing in this repo measures throughput, latency, or cache hit rate. Any performance characteristics are unmeasured.
- **No OpenAPI schema.** Use the Postman collection.

---

## Design decisions

**Why is a project the tenancy boundary rather than a user?**
Flags belong to teams, not individuals. Scoping by project lets several people share the same flags with different permissions, and makes flag keys unique per project instead of globally — so two teams can both own a `dark-mode` flag.

**Why does "not mine" return 404 instead of 403?**
A `403` confirms the resource exists. For a multi-tenant API, that leaks the existence of other teams' projects and flag keys, so anything you are not a member of is simply invisible.

**Why do individual targets not override the kill switch?**
The kill switch is what you reach for during an incident. If any targeting layer could override it, turning a flag off would no longer be a reliable way to stop it, so nothing sits above it.

**Why are prerequisites checked before individual targeting?**
Otherwise pinning a user to a flag would be a way to bypass a dependency, and the flag could serve to someone whose upstream feature is off.

**Why does an unresolvable reference never match?**
Inverting an unknown is the dangerous direction: a `not_in_segment` rule or an unmet prerequisite that "matched" on failure would turn a dangling reference into a full rollout. Uncertainty resolves to off, always.

**Why is rule-level bucketing salted with the rule id?**
Without a salt, two rules at the same percentage would hash identically and select exactly the same users, so a second 20% rollout would reach the same 20% of people. The flag-level rollout deliberately uses no salt — that hash decides who already has a flag, and changing its inputs would re-bucket everyone.

**Why do segments not nest?**
A segment referencing another segment would need recursive membership resolution and cycle detection of its own. Forbidding it keeps membership a single pass, and the flag-level prerequisite graph already covers dependency between features.

**Why SHA-256 for rollout bucketing?**
`SHA-256(flag_key + user_id) % 100` is deterministic, so the same user always lands in the same bucket for a given flag, and it is trivial to reimplement in any SDK language. LaunchDarkly uses MurmurHash3; SHA-256 is slower but available in every standard library.

**Why SHA-256 for SDK key storage?**
SDK keys are long-lived credentials, so storing raw values would turn any database breach into a full key compromise. Only the hash is persisted, and lookup is a single indexed query on the hash. The stored 16-char prefix lets a user identify a key without exposing the secret.

**Why does bulk download return evaluated results rather than raw flag config?**
Shipping the config would mean every SDK reimplements SHA-256 bucketing, rule
precedence, segment membership, and prerequisite cycle detection — and any
divergence between an SDK and the server is a flag serving the wrong value to
real users. Evaluating server-side keeps one implementation of the engine. The
cost is that an SDK cannot evaluate offline or re-evaluate a changed context
without another call.

**Why soft-delete (archive) instead of hard-delete?**
Hard-deleting a flag destroys its audit history, evaluation logs, and rule configuration. Archiving preserves all of it while removing the flag from evaluation and list responses.

**Why async evaluation logging?**
Writing an `EvaluationLog` row synchronously puts a DB write in the hot path of every flag check. Celery decouples the two: the HTTP response returns immediately and the write happens in a worker with retries. The trade-off is that a failed write is invisible to the caller.

**Why is the cache scoped to `(project_id, env_id, flag_key)`?**
Scoping by environment means toggling a flag in staging does not evict production's cached copy, so staging activity cannot cause production cache misses.

**Why is the cache invalidated on rule and segment changes too?**
The cached payload embeds the flag's rules, targets, prerequisites, and the segments its rules reference, so any of those writes makes it stale. Segment edits fan out to every flag referencing that segment in a single bulk query.

**Why are variation values stored in a JSONField?**
A variation must hold a boolean, string, number, or arbitrary JSON object. `JSONField` covers all four without a column per type, and `value_type` records which one is stored so clients can deserialise it.

**Why are boolean flags auto-wired on creation?**
`FlagService.create_flag()` gives every boolean flag `true`/`false` variations and wires them to `fallthrough_variation`/`off_variation`. Boolean flags work with zero configuration while the engine still runs one variation-based code path.

---

## Roadmap

**Built** — Phase 1 (foundational data model) and Phase 2 (targeting):

- Flag CRUD, multivariate flags, archive/soft-delete, per-environment state and toggle
- Version history with one-click rollback
- SDK key management with rotation
- Organizations, projects, and role-based access
- Individual user targeting
- Reusable segments
- Rule-level percentage rollout
- Prerequisite flags
- SDK client bootstrap (`POST /sdk/flags/evaluate/`) — every flag in an environment resolved for one user context

**Not built:**

- **SDK config download** (`GET /sdk/flags/config/`) — the raw ruleset, for server-side SDKs that evaluate in-process. The bootstrap endpoint above costs a round trip per *user context*, which is the wrong shape for a server SDK handling thousands of users per process. Specified in [SDK_CONFIG_SPEC.md](SDK_CONFIG_SPEC.md).
- **SDK infrastructure** — impression batching (bulk ingest of eval logs from an SDK), SSE streaming of flag updates.
- **Workflow** — stale flag detection, scheduled changes, webhooks, approval workflows.
- **Analytics** — impression aggregation, data export.
- **Experimentation** — A/B testing framework, statistical significance reporting.
- **Enterprise** — SSO and SCIM provisioning.

---

## License

[MIT](LICENSE) © Mohamed Samir
