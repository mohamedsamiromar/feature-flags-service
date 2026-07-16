# Feature Flag Engine — Complete Project Guide

> A single-source-of-truth walkthrough of **the whole project**: what it is, how it's
> wired together, **every endpoint** (why it exists, the idea behind it, and which
> services/models it touches), what is already built, and what remains.
>
> Everything here is derived directly from the code, not the README. Where the README
> and the code disagree, the code wins and it's noted.

---

## 1. What this project is

A **self-hosted, production-grade feature-flag backend**, modelled on LaunchDarkly.
An application points its SDK key at `POST /api/v1/sdk/evaluate/` and gets back a
per-user, per-environment answer for a flag — served from a Redis cache, with the
impression logged asynchronously and every config change tracked in an audit trail.

**Stack**

| Layer | Technology |
| --- | --- |
| API | Django 4.2 + Django REST Framework |
| Auth | JWT (`simplejwt`) for the dashboard · custom SDK-key auth for the SDK |
| Database | PostgreSQL 15 |
| Cache | Redis 7 |
| Async | Celery 5 (+ Celery Beat) on a Redis broker |
| Deploy | Docker Compose (`web`, `db`, `redis`, `celery`, `celery-beat`) |

**Base URL:** every dashboard endpoint is versioned under `/api/v1/`
(`API_VERSION` in `config/settings.py`). The health probe lives at the unversioned root.

---

## 2. The mental model (read this before the endpoints)

There are two very different kinds of caller, and the whole design flows from that split:

1. **The dashboard user** (a human / CI, authenticated with a **JWT**). They *configure*
   flags: create them, add variations, write targeting rules, manage environments and
   SDK keys. Every write goes through a **Service class** and is recorded by
   `AuditService`.

2. **The SDK** (an application in production, authenticated with an **`X-SDK-Key`**
   header). It only ever does one thing: *evaluate* a flag for a user. This is the hot
   path — cached, throttled, and logged asynchronously so the HTTP response never blocks
   on a DB write.

### The layering

```
HTTP request
   │
   ▼
View (thin — auth, validation, HTTP status)   apps/*/views.py
   │  delegates every write to…
   ▼
Service (all business logic + cache + audit)  apps/*/services.py
   │  reads/writes…
   ▼
Model (data + DB-level constraints)           apps/*/models.py
```

**Invariant:** business logic never lives in a view or a serializer. Views translate
HTTP↔Python and delegate; services do the real work. This is why the endpoint tables
below always name a service.

### The five cross-cutting invariants

These hold everywhere and explain a lot of the "why":

- **Ownership isolation** — every queryset is filtered by `owner=request.user` (or
  `…__owner=request.user`). This is the *only* multi-tenancy boundary today. No endpoint
  reads across users.
- **Service layer** — writes go through a `*Service`; `AuditService.log(...)` fires on
  every mutation.
- **Cache key = `flags:{owner_id}:{env_id}:{flag_key}`** — scoped per environment so a
  staging toggle can't invalidate the production cache. (Historical wart: two older
  helpers use an un-scoped `flags:{owner_id}:{flag_key}` key — see §7.)
- **Async evaluation logging** — the evaluate hot path writes `EvaluationLog` via a
  Celery task, never synchronously.
- **Three-layer numeric validation** — `rollout_percentage` (0–100) is enforced at the
  serializer, the model validator, *and* a PostgreSQL `CheckConstraint`.

---

## 3. The apps at a glance

| App | Responsibility | Exposes endpoints? |
| --- | --- | --- |
| `accounts` | Custom `User` model + JWT token URLs | Yes (token/refresh) |
| `core` | `BaseModel`, shared exceptions, `/healthz` | Yes (health only) |
| `flags` | `FeatureFlag` + `Variation`, `FlagService`, CRUD/archive/toggle/variations | Yes |
| `rules` | `Rule` model + CRUD (the targeting *config*) | Yes |
| `targeting` | `RuleEvaluator` — operator-matching *logic* (no endpoints yet) | No |
| `environment` | `Environment` + `EnvironmentFlag`, per-env state API | Yes |
| `sdk_keys` | `SDKKey` model, key generation/hashing, key-management API, SDK auth class | Yes |
| `sdk` | The single `POST /sdk/evaluate/` endpoint (SDK-key auth) | Yes |
| `evaluation` | `FlagEvaluationService` (the algorithm), `EvaluationLog`, Celery task, read-only log API | Yes (read logs) |
| `audit` | `AuditLog` model, `AuditService`, read-only audit API | Yes (read audit) |

Note: `targeting/models.py` still contains scaffold `Country`/`City` models — leftovers,
not used by the engine. The *real* targeting logic is `RuleEvaluator` in
`targeting/services.py`.

---

## 4. The models (data layer)

Every model inherits `core.BaseModel` (`id` BigAutoField, `created_at`, `updated_at`)
unless noted.

- **`accounts.User`** — `AbstractUser` subclass. The tenancy root; everything hangs off an owner.
- **`flags.FeatureFlag`** — `owner`, `name`, `key` (unique per owner), `flag_type`
  (`boolean` | `multivariate`), `is_archived`, plus **global** `is_enabled` /
  `rollout_percentage` fields and two FKs: `off_variation` and `fallthrough_variation`.
  DB constraints: unique `(owner, key)` and a `CheckConstraint` pinning
  `rollout_percentage` to 0–100.
- **`flags.Variation`** — belongs to a flag; has `name`, `value_type`
  (`boolean`/`string`/`number`/`json`) and a `value` stored in a `JSONField` (one column
  holds all four types). Unique `(flag, name)`.
- **`rules.Rule`** — belongs to a flag; `attribute`, `operator` (7 operators), `value`,
  `priority` (ordering), and optional `serve_variation` FK. Ordered by `priority`.
- **`environment.Environment`** — `owner` + `name` (development/staging/production),
  unique per owner.
- **`environment.EnvironmentFlag`** — the join of a flag × an environment, carrying the
  **per-environment** `is_enabled` and `rollout_percentage`. Unique `(feature_flag,
  environment)`. **This is what evaluation actually reads** — the flag's own
  `is_enabled`/`rollout_percentage` fields are effectively legacy/global defaults.
- **`sdk_keys.SDKKey`** — `name`, `prefix` (first 16 chars, shown in lists), `hashed_key`
  (SHA-256 of the full key, unique), `environment` FK, `key_type` (server/client),
  `is_active`, `last_used_at`. **The raw key is never stored.**
- **`evaluation.EvaluationLog`** — `flag`, `user`, `result` (bool), `context_data`
  (JSON), `evaluated_at`. Plain `models.Model` (not BaseModel).
- **`audit.AuditLog`** — `user`, `action`, `entity_type`, `entity_id`, `old_value`,
  `new_value` (both JSON snapshots).

---

## 5. Every endpoint

Format for each: **method + path**, why it exists / the idea, and the
**services & models** it hits.

### 5.1 Authentication — `apps/accounts` → `/api/v1/auth/`

These are DRF SimpleJWT's built-in views, wired directly in `accounts/urls.py`.

| Endpoint | Why / idea | Services & models |
| --- | --- | --- |
| `POST /api/v1/auth/token/` | Exchange username+password for an `access`+`refresh` JWT pair. The entry point for every dashboard call. | `TokenObtainPairView` (SimpleJWT) → `accounts.User` |
| `POST /api/v1/auth/token/refresh/` | Trade a valid `refresh` token for a fresh short-lived `access` token, so users don't re-login constantly. | `TokenRefreshView` (SimpleJWT) |

> **Idea:** short-lived access tokens limit the blast radius of a leaked token; the
> refresh token lets sessions stay alive without storing passwords anywhere.

### 5.2 Flags — `apps/flags` → `/api/v1/flags/`

`FeatureFlagViewSet` (a `ModelViewSet`, `lookup_field="key"`). All writes delegate to
`FlagService`; the toggle action reaches into `EnvironmentFlagService`. Queryset is
always `owner=request.user` and excludes archived flags unless `?include_archived=true`.

| Endpoint | Why / idea | Services & models |
| --- | --- | --- |
| `GET /flags/` | List the user's active flags. `?include_archived=true` surfaces archived ones. Prefetches `rules`. | Model: `FeatureFlag` (filtered by owner) |
| `POST /flags/` | Create a flag. **Idea:** a boolean flag should work with zero extra setup — so on creation `FlagService.create_flag` auto-creates `true`/`false` variations and wires them as fallthrough/off. Every create is audited. | `FlagService.create_flag` → `FeatureFlag`, `Variation`, `AuditService` |
| `GET /flags/{key}/` | Retrieve one flag by its human-readable key. | Model: `FeatureFlag` |
| `PATCH /flags/{key}/` | Update flag config (name, description, type, `off_variation`, `fallthrough_variation`, global rollout…). The view deliberately re-queries **including archived** flags so the service can return **409** on an archived flag instead of a misleading 404. Invalidates every env cache; audited. | `FlagService.update_flag` → `FeatureFlag`, `AuditService`, cache |
| `DELETE /flags/{key}/` | Hard-delete a flag. (Prefer archive — see below.) Invalidates cache and writes a `delete` audit row with the pre-delete snapshot. | `FlagService.delete_flag` → `FeatureFlag`, `AuditService`, cache |
| `POST /flags/{key}/archive/` | **Soft-delete.** Idea: hard delete destroys audit history, eval logs, and rules. Archiving pulls the flag out of lists + evaluation while keeping all history. Double-archive → 409. | `FlagService.archive_flag` → `FeatureFlag`, `AuditService`, cache |
| `POST /flags/{key}/unarchive/` | One-call restore of an archived flag, zero data loss. Unarchiving a non-archived flag → 409. | `FlagService.unarchive_flag` → `FeatureFlag`, `AuditService`, cache |
| `POST /flags/{key}/toggle/` | **One-call kill switch per environment.** Body `{"environment": "production"}`. Idea: flipping a flag on/off is the single most common operation, so it gets a dedicated verb instead of a full PATCH on env state. The `EnvironmentFlag` is created on first toggle (defaults off, so the first call turns it **on**). Archived flag → 409; missing env → 404. | `EnvironmentFlagService.toggle` → `Environment`, `EnvironmentFlag`, `AuditService`, cache |
| `GET /flags/{key}/variations/` | List a flag's variations. | Model: `Variation` |
| `POST /flags/{key}/variations/` | Add a named typed variation (for multivariate flags). Invalidates env caches. | `FlagService.create_variation` → `Variation`, cache |
| `PATCH /flags/{key}/variations/{id}/` | Edit a variation's name/type/value. | `FlagService.update_variation` → `Variation`, cache |
| `DELETE /flags/{key}/variations/{id}/` | Remove a variation. | `FlagService.delete_variation` → `Variation`, cache |

> **Note:** the variation write endpoints go through `FlagService` (cache invalidation)
> but **do not** currently call `AuditService` — variation changes are not audited today.

### 5.3 Rules — `apps/rules` → `/api/v1/rules/`

`RuleViewSet` (`ModelViewSet`). This is the *targeting configuration* API. Queryset is
scoped by `flag__owner=request.user`, and `select_related("flag")` is required so the
cache-invalidation helpers can read `flag.owner_id`/`flag.key` without extra queries.

| Endpoint | Why / idea | Services & models |
| --- | --- | --- |
| `GET /rules/` | List all rules across the user's flags. | Model: `Rule` (filtered by `flag__owner`) |
| `POST /rules/` | Create a targeting rule: "if user's `{attribute}` `{operator}` `{value}`, serve `{serve_variation}`". Cross-user protection lives in `RuleSerializer.validate_flag()`. Creating a rule invalidates the flag's cache so the next evaluation re-reads it. | Model: `Rule`, `Variation`; then `FlagService._invalidate_cache` |
| `GET /rules/{id}/` | Retrieve a rule. | Model: `Rule` |
| `PATCH /rules/{id}/` | Update a rule (attribute/operator/value/priority/serve_variation). Invalidates cache. | Model: `Rule`; `FlagService._invalidate_cache` |
| `DELETE /rules/{id}/` | Delete a rule. Captures owner_id/key *before* delete, then invalidates cache. | Model: `Rule`; `FlagService._invalidate_cache` |

> **Idea:** rules are the flag's targeting brain, but the *runtime* matching logic lives
> in `targeting.RuleEvaluator` (operators `eq`, `neq`, `contains`, `in`, `not_in`, `gt`,
> `lt`). The rules app only stores/edits config; evaluation reads a cached copy.
>
> **Wart:** these use `FlagService._invalidate_cache(owner_id, key)`, the **un-scoped**
> key format — see §7.

### 5.4 Environments — `apps/environment` → `/api/v1/environments/`

`EnvironmentViewSet` (create/list/retrieve/destroy + two custom flag actions). All
per-env flag writes go through `EnvironmentFlagService`.

| Endpoint | Why / idea | Services & models |
| --- | --- | --- |
| `GET /environments/` | List the user's environments. | Model: `Environment` |
| `POST /environments/` | Create an environment (development/staging/production). Idea: the same flag behaves differently per environment — this is the container that makes that possible. | Model: `Environment` |
| `GET /environments/{id}/` | Retrieve an environment. | Model: `Environment` |
| `DELETE /environments/{id}/` | Delete an environment. **Cascades** to its SDK keys and per-env flag states. | Model: `Environment` (cascade → `SDKKey`, `EnvironmentFlag`) |
| `GET /environments/{id}/flags/` | List the per-environment state (`is_enabled`, `rollout_percentage`) of every flag in this environment. | Model: `EnvironmentFlag` (+ `feature_flag`) |
| `PATCH /environments/{id}/flags/{flag_id}/` | Set a flag's `is_enabled`/`rollout_percentage` **for this environment specifically**. The generic way to change env state (toggle is the shortcut for the on/off case). Invalidates that env's cache. | `EnvironmentFlagService.update_state` → `EnvironmentFlag`, cache |

### 5.5 SDK Keys — `apps/sdk_keys` → `/api/v1/sdk-keys/`

`SDKKeyViewSet` (create/list/retrieve + revoke/rotate actions). All logic in
`SDKKeyService`; key material is generated/hashed in `KeyGenerator`.

| Endpoint | Why / idea | Services & models |
| --- | --- | --- |
| `POST /sdk-keys/` | Mint a key for an environment. **Idea:** the raw key is returned **exactly once** in this response and never stored — only its SHA-256 hash + 16-char prefix are persisted, so a DB breach can't leak live credentials. | `SDKKeyService.create_key` → `KeyGenerator`, `SDKKey`, `Environment` |
| `GET /sdk-keys/` | List keys — **prefix only**, never the secret. Scoped by `environment__owner`. | Model: `SDKKey` |
| `GET /sdk-keys/{id}/` | Retrieve one key's metadata. | Model: `SDKKey` |
| `POST /sdk-keys/{id}/revoke/` | Immediately deactivate a key (`is_active=False`). Double-revoke → 409. | `SDKKeyService.revoke` → `SDKKey` |
| `POST /sdk-keys/{id}/rotate/` | Atomically revoke the old key and issue a replacement with the same metadata — returns the new full key once. Idea: rotate a credential without a window where no key works. | `SDKKeyService.rotate` → `SDKKeyService.revoke` + `create_key` → `SDKKey`, `KeyGenerator` |

### 5.6 SDK Evaluate — `apps/sdk` → `/api/v1/sdk/evaluate/`

The **product's whole reason to exist**. `SDKEvaluateFlagView` (`APIView`), authenticated
by `SDKKeyAuthentication` (the `X-SDK-Key` header), throttled by a dedicated
`ScopedRateThrottle` (scope `evaluation`, default ~1000/min).

| Endpoint | Why / idea | Services & models |
| --- | --- | --- |
| `POST /sdk/evaluate/` | Answer "what value does *this user* get for *this flag* in *this environment*?" **Idea:** the environment is derived from the SDK key itself, so callers never pass `env_id` — the key *is* the environment scope. Runs the evaluation algorithm (cache→kill-switch→rules→rollout→default), returns typed `{result, result_type}`, and fires the impression log asynchronously so the response never waits on the DB. Archived/missing flag → 404. | Auth: `SDKKeyAuthentication` → `SDKKey`, `Environment`. Eval: `FlagEvaluationService.evaluate` → `EnvironmentFlag`, `Variation`, `RuleEvaluator`, Redis. Logging: `log_evaluation.delay(...)` Celery task → `EvaluationLog` |

**The evaluation algorithm** (`FlagEvaluationService.evaluate`), in order:
1. **Cache lookup** `flags:{owner}:{env}:{key}` in Redis; on miss, load from PostgreSQL
   (joining `EnvironmentFlag` → flag → variations → rules) and warm the cache (TTL 300s).
2. **Kill switch** — if the env's `is_enabled` is false, return `off_variation`.
3. **Targeting rules** in `priority` order — first match serves its `serve_variation`
   (or the flag's `fallthrough_variation`).
4. **Percentage rollout** — `SHA-256(flag_key + user_id) % 100 < rollout_percentage`.
   Deterministic: a given user always lands in the same bucket. In-bucket →
   `fallthrough_variation`; out → `off_variation`.
5. Legacy fallback: a flag with no variations returns raw `true`/`false` booleans.

### 5.7 Evaluation Logs — `apps/evaluation` → `/api/v1/evaluation/logs/`

`EvaluationLogViewSet` (read-only list/retrieve), scoped by `flag__owner`.

| Endpoint | Why / idea | Services & models |
| --- | --- | --- |
| `GET /evaluation/logs/` | Inspect past evaluations (which user got which result, with their context). Populated **asynchronously** by the Celery `log_evaluation` task, so reads may lag the live traffic by a moment. | Model: `EvaluationLog` (+ `flag`) |
| `GET /evaluation/logs/{id}/` | Retrieve a single evaluation record. | Model: `EvaluationLog` |

### 5.8 Audit — `apps/audit` → `/api/v1/audit/`

`AuditLogViewSet` (read-only), scoped by `user=request.user`.

| Endpoint | Why / idea | Services & models |
| --- | --- | --- |
| `GET /audit/` | The change history — who changed what, with before/after JSON snapshots. Written centrally by `AuditService.log` on every mutation. | Model: `AuditLog` |
| `GET /audit/{id}/` | Retrieve a single audit entry. | Model: `AuditLog` |

> **Idea:** a single `AuditService` is the only writer, so audit coverage can't drift
> per-view. Actions recorded today: `create`, `update`, `delete`, `archive`,
> `unarchive`, `toggle`.

### 5.9 Health — `apps/core` → `/healthz/` (unversioned, no auth)

| Endpoint | Why / idea | Services & models |
| --- | --- | --- |
| `GET /healthz/` | Liveness/readiness probe for load balancers, k8s, and Docker `HEALTHCHECK`. Runs `SELECT 1` against PostgreSQL and a write/read sentinel against Redis. `200` if both pass, `503` otherwise. Auth is intentionally off and throttling disabled so probes always get through. | Direct DB cursor + Redis `cache`; no models |

---

## 6. What is already built ✅

**Core engine**
- Flag CRUD keyed by human-readable `key`, scoped per owner.
- Deterministic SHA-256 percentage rollout (same user → same bucket).
- Rule-based targeting with 7 operators, evaluated by priority.
- Redis caching scoped per `(owner, env, key)`, invalidated on every relevant write.
- The full 5-step evaluation algorithm with typed results.

**Multivariate flags (F-07)** — `boolean` vs `multivariate` flags; `Variation` model with
JSON-stored typed values; off/fallthrough wiring; rule-level `serve_variation`; typed
`{result, result_type}` responses; backwards-compatible boolean fallback.

**Flag lifecycle (F-06)** — archive/unarchive soft-delete; archived flags hidden from
lists and 404'd from evaluation; 409 on mutating an archived flag; audited.

**Multi-environment** — `Environment` + `EnvironmentFlag` per-env state; env-scoped cache;
per-env PATCH and the one-call `toggle` shortcut; cascade delete.

**SDK keys (F-03)** — server/client keys scoped to one environment; hash-only storage
with once-shown raw key; revoke; atomic rotate; `last_used_at` tracking; custom
`X-SDK-Key` auth class.

**Security & auth** — JWT for dashboard; SDK-key auth for evaluate; ownership isolation
everywhere; cross-user rule-assignment block; dedicated rate limit on evaluate;
three-layer rollout validation.

**Observability** — audit trail with before/after snapshots; async evaluation logging via
Celery with retry/back-off; read-only audit + eval-log APIs.

**Infra/ops** — `/healthz` DB+Redis probe; env-var-driven config; persistent DB
connections (`CONN_MAX_AGE`); compound DB indexes on rules/eval-logs/audit; Docker
Compose with `web`/`db`/`redis`/`celery`/`celery-beat`; Postman collection.

Per the project memory: **core engine complete (2026-05-25)**, **143 tests passing**.

---

## 7. Known rough edges (worth knowing before you extend)

- **Two cache-key formats coexist.** The correct, env-scoped format is
  `flags:{owner}:{env}:{key}` (`FlagEvaluationService.invalidate_cache`,
  `EnvironmentFlagService`, and `FlagService._invalidate_all_env_caches`). But
  `FlagService._invalidate_cache` and `RuleViewSet` still use the **un-scoped**
  `flags:{owner}:{key}` — which the evaluate path never writes. Net effect: **rule
  create/update/delete and flag hard-delete don't actually bust the evaluation cache**;
  stale rules can be served for up to the 300s TTL. When adding mutations, prefer
  `_invalidate_all_env_caches`.
- **Variation writes aren't audited.** `create/update/delete_variation` invalidate cache
  but skip `AuditService`.
- **`FeatureFlag.is_enabled` / `rollout_percentage` are quasi-legacy.** Evaluation reads
  the `EnvironmentFlag` values, not these. They serve as global defaults / the
  three-layer-validation showcase.
- **`targeting` app has leftover scaffold models** (`Country`, `City`) unrelated to the
  engine.

---

## 8. What remains (roadmap)

Straight from `README.md`, with Phase-1 status reflecting the current code.

**Phase 1 — foundational data model** (nearly done)
- [ ] Flag **version history + one-click rollback** ← *remaining*
- [ ] **Projects & Organizations** (team-level multi-tenancy) ← *remaining*
- [x] Archive/soft-delete, toggle endpoint, environments, SDK keys, multivariate flags

**Phase 2 — targeting power** (not started)
- [ ] Individual user targeting (allow/deny list per flag)
- [ ] Reusable segments (define a user group once, reuse across flags)
- [ ] Prerequisite flags (flag B evaluates only if flag A resolves a certain way)
- [ ] Rule-level percentage rollout within a matched segment

**Phase 3 — real-time SDK infra**
- [ ] Impression **batching** endpoint (bulk eval-log ingest)
- [ ] Server-side SDK **bulk download** (`GET /sdk/flags/`)
- [ ] **SSE streaming** — push flag updates to connected SDKs

**Phase 4 — workflow & governance**
- [ ] Stale-flag detection (Celery-beat job)
- [ ] Scheduled flag changes (enable at a datetime)
- [ ] Webhook notifications on mutations
- [ ] Approval workflows for production changes

**Phase 5 — observability & analytics**
- [ ] Impression aggregation (hourly rollup + stats endpoint)
- [ ] Data export to S3 / BigQuery

**Phase 6 — experimentation**
- [ ] A/B testing framework (flags ↔ experiments ↔ metrics)
- [ ] Statistical significance reporting (frequentist Z-test)

**Phase 7 — enterprise**
- [ ] RBAC (admin/writer/reader per project)
- [ ] SSO + SCIM provisioning

### The single most impactful next endpoint

There is **no self-serve user-registration endpoint** — users are created via
`createsuperuser`/admin. For a backend meant to be adopted "in minutes," a
`POST /api/v1/auth/register/` is the most obvious gap after the Phase-1 items.

---

## 9. Endpoint index (quick reference)

```
# Auth (JWT)
POST   /api/v1/auth/token/
POST   /api/v1/auth/token/refresh/

# Flags
GET    /api/v1/flags/                          (?include_archived=true)
POST   /api/v1/flags/
GET    /api/v1/flags/{key}/
PATCH  /api/v1/flags/{key}/                     (409 if archived)
DELETE /api/v1/flags/{key}/
POST   /api/v1/flags/{key}/archive/            (409 if already archived)
POST   /api/v1/flags/{key}/unarchive/
POST   /api/v1/flags/{key}/toggle/             body: {"environment": "..."}
GET    /api/v1/flags/{key}/variations/
POST   /api/v1/flags/{key}/variations/
PATCH  /api/v1/flags/{key}/variations/{id}/
DELETE /api/v1/flags/{key}/variations/{id}/

# Rules
GET    /api/v1/rules/
POST   /api/v1/rules/
GET    /api/v1/rules/{id}/
PATCH  /api/v1/rules/{id}/
DELETE /api/v1/rules/{id}/

# Environments
GET    /api/v1/environments/
POST   /api/v1/environments/
GET    /api/v1/environments/{id}/
DELETE /api/v1/environments/{id}/
GET    /api/v1/environments/{id}/flags/
PATCH  /api/v1/environments/{id}/flags/{flag_id}/

# SDK keys
POST   /api/v1/sdk-keys/                        (full key once)
GET    /api/v1/sdk-keys/
GET    /api/v1/sdk-keys/{id}/
POST   /api/v1/sdk-keys/{id}/revoke/           (409 if already revoked)
POST   /api/v1/sdk-keys/{id}/rotate/

# SDK evaluate (X-SDK-Key auth, throttled)
POST   /api/v1/sdk/evaluate/

# Logs & audit (read-only)
GET    /api/v1/evaluation/logs/
GET    /api/v1/evaluation/logs/{id}/
GET    /api/v1/audit/
GET    /api/v1/audit/{id}/

# Infrastructure (no auth, unversioned)
GET    /healthz/
```
