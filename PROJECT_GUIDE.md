# Feature Flag Engine — Complete Project Guide

> A single-source-of-truth walkthrough of **the whole project**: what it is, how it's
> wired together, **every endpoint** (why it exists, the idea behind it, and which
> services/models it touches), what is already built, and what remains.
>
> Everything here is derived directly from the code. Where the README and the code
> disagree, the code wins and it's noted.

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

### Two kinds of caller

1. **The dashboard user** (a human / CI, authenticated with a **JWT**). They *configure*
   flags: create them, add variations, write targeting rules, define segments, manage
   environments and SDK keys. Every write goes through a **Service class**.

2. **The SDK** (an application in production, authenticated with an **`X-SDK-Key`**
   header). It only ever does one thing: *evaluate* a flag for a user. This is the hot
   path — cached, throttled, and logged asynchronously so the HTTP response never blocks
   on a DB write. The key **is** the principal; there is no user behind an SDK request.

### The tenancy model

```text
Organization ── Membership(user, role) ── owner | admin | member | viewer
     │
     └── Project ──┬── FeatureFlag ──┬── Variation
                   │                 ├── FlagTarget        (individual users)
                   │                 ├── Rule              (targeting rules)
                   │                 ├── FlagPrerequisite  (gates)
                   │                 └── FlagVersion       (history)
                   ├── Segment ──────┬── SegmentTarget
                   │                 └── SegmentRule
                   └── Environment ──┬── EnvironmentFlag   (per-env state)
                                     └── SDKKey
```

**The project is the tenancy boundary.** Flag keys are unique per project, so two teams
can each own a `dark-mode`. A project you are not a member of returns **404**, not 403 —
a 403 would confirm it exists and leak other teams' project and flag keys. A member
whose *role* is too low gets **403**.

### The four layers

```text
HTTP request
   │
   ▼
View        request/response only — no `if`, no `try`, no ORM      apps/*/views.py
   │
   ▼
Serializer  (de)serialisation + field shape only — no DB           apps/*/serializers.py
   │
   ▼
Service     `*Service` — all business logic, cache, audit; no ORM  apps/*/services.py
   │
   ▼
Query       `*Query` — the ONLY place that touches the ORM         apps/*/queries.py
```

**Invariant:** services take identifiers (`key` / `id` + `user`), fetch through the query
layer, and raise `APIError`. Cross-entity checks ("does this variation belong to this
flag?") live in the service, never the serializer. New endpoints add ORM code only in
`queries.py`.

### Error handling

`apps/core/errors.py` holds an `Error` enum — each entry carrying a stable negative
`code`, an `http_status`, and a translatable `detail` with `{}` placeholders — plus a
single `APIError(error, extra=[...])` exception. Services raise `APIError`;
`config/exception_handler.api_exception_handler` renders `{code, detail, alert}` with the
declared status. Views need no `try/except`.

### The cross-cutting invariants

- **Membership scoping** — every queryset is filtered by project membership; role is
  asserted by `AccessService` before any mutation.
- **Service layer** — writes go through a `*Service`.
- **Cache key = `flags:{project_id}:{env_id}:{flag_key}`** — one format, three call
  sites. Scoped per environment so a staging toggle can't evict production's copy.
- **Async evaluation logging** — the evaluate hot path writes `EvaluationLog` via a
  Celery task, never synchronously. Celery serialises task arguments as JSON, so the
  cached flag config (which contains Python sets) must never be passed to a task.
- **Three-layer numeric validation** — `rollout_percentage`, on both `FeatureFlag` and
  `Rule`, is enforced at the serializer, the model validator, *and* a PostgreSQL
  `CheckConstraint`.
- **Uncertainty fails closed** — an unresolvable prerequisite or segment reference
  leaves the flag off. Never invert an unknown.

---

## 3. The apps at a glance

| App | Responsibility | Exposes endpoints? |
| --- | --- | --- |
| `accounts` | Custom `User` model + JWT token URLs | Yes (token/refresh) |
| `organizations` | `Organization`, `Membership`, `Project`, `AccessService` (RBAC) | Yes |
| `core` | `BaseModel`, `Error`/`APIError`, `/healthz` | Yes (health only) |
| `flags` | `FeatureFlag`, `Variation`, `FlagTarget`, `FlagPrerequisite`, `FlagVersion` | Yes |
| `rules` | `Rule` model + CRUD (the targeting *config*) | Yes |
| `segments` | `Segment`, `SegmentTarget`, `SegmentRule`, `SegmentEvaluator` | Yes |
| `targeting` | `RuleEvaluator` — operator-matching *logic* | No |
| `environment` | `Environment` + `EnvironmentFlag`, per-env state API | Yes |
| `sdk_keys` | `SDKKey` model, key generation/hashing, SDK auth class | Yes |
| `sdk` | The SDK-facing endpoints: `POST /sdk/evaluate/` (one flag) and `POST /sdk/flags/evaluate/` (bulk download) | Yes |
| `evaluation` | `FlagEvaluationService` (the algorithm), `EvaluationLog`, Celery task | Yes (read logs) |
| `audit` | `AuditLog` model, `AuditService`, read-only audit API | Yes (read audit) |

Note: `targeting/models.py` still contains scaffold `Country`/`City` models — leftovers,
not used by the engine. The real targeting logic is `RuleEvaluator` in
`targeting/services.py`.

---

## 4. The models

Every model inherits `core.BaseModel` (`id`, `created_at`, `updated_at`) unless noted.

**Tenancy**

- **`accounts.User`** — `AbstractUser` subclass.
- **`organizations.Organization`** — `name`, unique `slug`.
- **`organizations.Membership`** — `(organization, user, role)`. `Role` is
  `viewer < member < admin < owner`, compared by `Role.rank`.
- **`organizations.Project`** — belongs to an organization; `name` and a **globally
  unique** `key`, so a project is addressable as `/projects/{key}/` without also
  threading the org slug through every URL.

**Flags**

- **`flags.FeatureFlag`** — `project`, `name`, `key`, `flag_type`
  (`boolean` | `multivariate`), `is_archived`, global `is_enabled` /
  `rollout_percentage`, and `off_variation` / `fallthrough_variation` FKs.
  Constraints: unique `(project, key)` and `rollout_percentage` 0–100.
- **`flags.Variation`** — belongs to a flag; `name`, `value_type`
  (`boolean`/`string`/`number`/`json`), and a `value` in a `JSONField`. Unique
  `(flag, name)`.
- **`flags.FlagTarget`** — `(flag, variation, user_key)`, unique `(flag, user_key)`.
  One user, pinned to one variation. The unique index doubles as the lookup index.
- **`flags.FlagPrerequisite`** — `(flag, prerequisite_flag, required_variation)`, unique
  `(flag, prerequisite_flag)`, plus a `prerequisite_is_not_self` check.
- **`flags.FlagVersion`** — immutable config snapshot; `version_no`, `snapshot`,
  `change_action` (`create`/`update`/`rollback`), `source_version_no`, `changed_by`.

**Targeting**

- **`rules.Rule`** — belongs to a flag; `attribute`, `operator`, `value`, `priority`,
  optional `serve_variation`, and its own `rollout_percentage` (default 100).
  `attribute` may be blank **only** for the segment operators.
- **`segments.Segment`** — belongs to a project; `name`, `key`, unique `(project, key)`.
- **`segments.SegmentTarget`** — `(segment, user_key, excluded)`, unique
  `(segment, user_key)`, so a user can never be both included and excluded.
- **`segments.SegmentRule`** — `(segment, attribute, operator, value)`. Operators are
  `NON_SEGMENT_OPERATOR_CHOICES`: segments deliberately do not nest.

**Environments & keys**

- **`environment.Environment`** — `project` + `name`, unique per project.
- **`environment.EnvironmentFlag`** — the join of flag × environment carrying the
  **per-environment** `is_enabled` and `rollout_percentage`. **This is what evaluation
  actually reads** — the flag's own fields are global defaults.
- **`sdk_keys.SDKKey`** — `name`, `prefix`, `hashed_key` (SHA-256, unique),
  `environment`, `key_type` (server/client), `is_active`, `last_used_at`. **The raw key
  is never stored.**

**Observability**

- **`evaluation.EvaluationLog`** — `flag`, `user` (None for SDK calls), `result`,
  `context_data`, `evaluated_at`. Plain `models.Model`.
- **`audit.AuditLog`** — `user`, `action`, `entity_type`, `entity_id`, `old_value`,
  `new_value`.

---

## 5. Every endpoint

### 5.1 Authentication — `/api/v1/auth/`

| Endpoint | Why / idea | Services & models |
| --- | --- | --- |
| `POST /auth/token/` | Exchange username+password for an `access`+`refresh` JWT pair. | `TokenObtainPairView` → `accounts.User` |
| `POST /auth/token/refresh/` | Trade a refresh token for a fresh access token. | `TokenRefreshView` |

> Short-lived access tokens limit the blast radius of a leak; the refresh token keeps
> sessions alive without storing passwords. **There is no registration endpoint** — see §8.

### 5.2 Organizations & projects — `apps/organizations`

| Endpoint | Why / idea | Services & models |
| --- | --- | --- |
| `GET /organizations/` | List organizations you belong to. | `OrganizationQuery.list_for_member` |
| `POST /organizations/` | Create one. The creator becomes its `owner`. | `OrganizationService.create` → `Organization`, `Membership` |
| `GET /organizations/{slug}/` | Retrieve. Not a member → 404. | `OrganizationQuery.get_for_member` |
| `DELETE /organizations/{slug}/` | Delete. **Owner only.** Cascades to everything below it. | `AccessService.assert_is_owner` |
| `GET /organizations/{slug}/members/` | List members and their roles. | `MembershipQuery` |
| `POST /organizations/{slug}/members/` | Add a member with a role. **Admin+.** | `OrganizationService.add_member` |
| `PATCH /organizations/{slug}/members/{user_id}/` | Change a role. **Admin+.** Demoting the last owner → 409 `LAST_OWNER`. | `OrganizationService.change_role` |
| `DELETE /organizations/{slug}/members/{user_id}/` | Remove a member. **Admin+.** Removing the last owner → 409. | `OrganizationService.remove_member` |
| `GET /projects/` | List projects in organizations you belong to. | `ProjectQuery.list_for_member` |
| `POST /projects/` | Create a project in an organization. **Admin+.** | `ProjectService.create` → `Project` |
| `GET /projects/{key}/` | Retrieve a project. | `ProjectQuery.get_for_member` |
| `DELETE /projects/{key}/` | Delete a project. **Admin+.** Cascades to flags, environments, segments. | `ProjectService.delete` |

### 5.3 Flags — `/api/v1/projects/{project_key}/flags/`

`FeatureFlagViewSet` (`lookup_field="key"`). All writes delegate to `FlagService`,
which resolves the project, asserts role, and invalidates caches.

| Endpoint | Why / idea | Services & models |
| --- | --- | --- |
| `GET /flags/` | List the project's active flags. `?include_archived=true` includes archived. | `FlagQuery.list_for_project` |
| `POST /flags/` | Create a flag. A boolean flag should work with zero setup, so creation auto-creates `true`/`false` variations and wires them as fallthrough/off. Audited; snapshots v1. | `FlagService.create_flag` → `FeatureFlag`, `Variation`, `FlagVersion`, `AuditService` |
| `GET /flags/{key}/` | Retrieve one flag by its human-readable key. | `FlagQuery.get_in_project` |
| `PATCH /flags/{key}/` | Update flag config. **`key` is immutable** — it addresses SDK calls, cache entries, and every version snapshot, so changing it returns 400 `IMMUTABLE_FIELD`. Archived → 409. Invalidates every env cache; audited; snapshots a version. | `FlagService.update_flag` |
| `DELETE /flags/{key}/` | Hard-delete. Refused with 409 if the flag gates another flag. Prefer archive. | `FlagService.delete_flag` |
| `POST /flags/{key}/archive/` | **Soft-delete.** Hard delete destroys audit history, eval logs, and rules; archiving pulls the flag out of lists and evaluation while keeping all of it. Refused if the flag gates another. Double-archive → 409. | `FlagService.archive_flag` |
| `POST /flags/{key}/unarchive/` | One-call restore, zero data loss. | `FlagService.unarchive_flag` |
| `POST /flags/{key}/toggle/` | **One-call kill switch per environment.** Body `{"environment": "production"}`. The `EnvironmentFlag` is created on first toggle (off by default, so the first call turns it **on**). | `EnvironmentFlagService.toggle` |

**Variations**

| Endpoint | Why / idea | Services & models |
| --- | --- | --- |
| `GET /flags/{key}/variations/` | List a flag's variations. | `VariationQuery` |
| `POST /flags/{key}/variations/` | Add a named typed variation. Invalidates caches; audited. | `FlagService.create_variation` |
| `PATCH /flags/{key}/variations/{id}/` | Edit name/type/value. Audited. | `FlagService.update_variation` |
| `DELETE /flags/{key}/variations/{id}/` | Remove it. Cascades to any target or prerequisite pointing at it. Audited. | `FlagService.delete_variation` |

**Individual targeting**

| Endpoint | Why / idea | Services & models |
| --- | --- | --- |
| `GET /flags/{key}/targets/` | List individually targeted users. | `FlagTargetQuery.list_for_flag` |
| `PUT /flags/{key}/targets/` | Pin `user_key` to a variation, overriding rules and rollout for that user alone. Idempotent: 201 on create, 200 on re-target. Body `{"user_key": ..., "variation": id}`. | `FlagService.set_target` |
| `DELETE /flags/{key}/targets/{user_key}/` | Remove the pin. | `FlagService.remove_target` |

> The route uses `[^/]+` rather than DRF's default `[^/.]+`, which would truncate every
> email-shaped user key at the first dot.

**Prerequisites**

| Endpoint | Why / idea | Services & models |
| --- | --- | --- |
| `GET /flags/{key}/prerequisites/` | List the gates on this flag. | `FlagPrerequisiteQuery.list_for_flag` |
| `PUT /flags/{key}/prerequisites/` | Gate this flag behind another serving a specific variation. Body `{"prerequisite_key": ..., "variation_id": ...}`. Rejects self-reference, cross-project prerequisites, a variation that isn't the prerequisite's own, and any edge closing a cycle (409 `CIRCULAR_PREREQUISITE`, reporting the path). | `FlagService.add_prerequisite` → `FlagPrerequisiteQuery.reaches` |
| `DELETE /flags/{key}/prerequisites/{prerequisite_key}/` | Remove the gate. | `FlagService.remove_prerequisite` |

**Version history**

| Endpoint | Why / idea | Services & models |
| --- | --- | --- |
| `GET /flags/{key}/versions/` | History, newest first. | `FlagVersionQuery` |
| `GET /flags/{key}/versions/{n}/` | One snapshot, with who changed it and when. | `FlagVersionQuery.get` |
| `POST /flags/{key}/versions/{n}/rollback/` | Restore that snapshot. **Append-only**: writes a new `rollback` version recording `source_version_no` rather than rewriting history. Dangling variation refs are dropped to `null`. Archived → 409. | `FlagService.rollback` |

### 5.4 Segments — `/api/v1/projects/{project_key}/segments/`

A segment names a group of users once so many flags can target it.

| Endpoint | Why / idea | Services & models |
| --- | --- | --- |
| `GET /segments/` | List the project's segments with their members and rules. | `SegmentQuery.list_for_project` |
| `POST /segments/` | Create one. Duplicate key in the project → 409. | `SegmentService.create_segment` |
| `GET /segments/{key}/` | Retrieve, embedding targets and rules. | `SegmentQuery.get_in_project` |
| `PATCH /segments/{key}/` | Update name/description. **`key` is immutable** — rules reference a segment by key, so changing it would orphan them (400). | `SegmentService.update_segment` |
| `DELETE /segments/{key}/` | Delete. Refused with 409 `SEGMENT_IN_USE` while any rule references it, so a reference can never dangle. | `SegmentService.delete_segment` |
| `GET /segments/{key}/targets/` | List explicitly named members. | `SegmentTargetQuery` |
| `PUT /segments/{key}/targets/` | Include or exclude a user. Idempotent; moving a user between the lists updates one row. | `SegmentService.set_target` |
| `DELETE /segments/{key}/targets/{user_key}/` | Remove a named member. | `SegmentService.remove_target` |
| `GET /segments/{key}/rules/` | List the segment's attribute rules. | `SegmentRuleQuery` |
| `POST /segments/{key}/rules/` | Add an attribute condition. Segment operators are rejected — segments do not nest. | `SegmentService.create_rule` |
| `PATCH /segments/{key}/rules/{id}/` | Update a condition. | `SegmentService.update_rule` |
| `DELETE /segments/{key}/rules/{id}/` | Delete a condition. | `SegmentService.delete_rule` |

> **Every** segment mutation evicts the cache of every flag whose rules reference that
> segment, resolved in one bulk query. Without it a membership change would wait out the
> full 300s TTL.

### 5.5 Targeting rules — `/api/v1/rules/`

The targeting *configuration* API. Querysets are scoped by project membership.

| Endpoint | Why / idea | Services & models |
| --- | --- | --- |
| `GET /rules/` | List rules across flags you can see. | `RuleQuery.list_for_member` |
| `POST /rules/` | Create a rule: "if `{attribute}` `{operator}` `{value}`, serve `{serve_variation}` to `{rollout_percentage}`% of matches". For `in_segment`/`not_in_segment`, `value` is a segment key and `attribute` may be blank; an unknown segment → 400. | `RuleService.create` → `Rule` |
| `GET /rules/{id}/` | Retrieve a rule. | `RuleQuery` |
| `PATCH /rules/{id}/` | Update it. Invalidates the flag's caches. | `RuleService.update` |
| `DELETE /rules/{id}/` | Delete it. Invalidates the flag's caches. | `RuleService.delete` |

> Operators: `eq`, `neq`, `contains`, `in`, `not_in`, `gt`, `lt`, `in_segment`,
> `not_in_segment`. Matching lives in `targeting.RuleEvaluator`; segment membership in
> `segments.SegmentEvaluator`.

### 5.6 Environments — `/api/v1/projects/{project_key}/environments/`

| Endpoint | Why / idea | Services & models |
| --- | --- | --- |
| `GET /environments/` | List the project's environments. | `EnvironmentQuery.list_for_project` |
| `POST /environments/` | Create one. The same flag behaves differently per environment; this is the container that makes that possible. | `EnvironmentService.create` |
| `GET /environments/{id}/` | Retrieve. | `EnvironmentQuery` |
| `DELETE /environments/{id}/` | Delete. **Cascades** to its SDK keys and per-env flag states. | `EnvironmentService.delete` |
| `GET /environments/{id}/flags/` | Per-environment state of every flag here. | `EnvironmentFlag` |
| `PATCH /environments/{id}/flags/{flag_id}/` | Set `is_enabled`/`rollout_percentage` **for this environment**. The general form of `toggle`. | `EnvironmentFlagService.update_state` |

### 5.7 SDK keys — `/api/v1/sdk-keys/`

| Endpoint | Why / idea | Services & models |
| --- | --- | --- |
| `POST /sdk-keys/` | Mint a key for an environment. The raw key is returned **exactly once** and never stored — only a SHA-256 hash and a 16-char prefix persist, so a DB breach can't leak live credentials. | `SDKKeyService.create_key` → `KeyGenerator` |
| `GET /sdk-keys/` | List keys — **prefix only**, never the secret. | `SDKKey` |
| `GET /sdk-keys/{id}/` | Retrieve one key's metadata. | `SDKKey` |
| `POST /sdk-keys/{id}/revoke/` | Deactivate immediately. Double-revoke → 409. | `SDKKeyService.revoke` |
| `POST /sdk-keys/{id}/rotate/` | Atomically revoke and issue a replacement, returning the new key once — rotate without a window where no key works. | `SDKKeyService.rotate` |

### 5.8 SDK — `/api/v1/sdk/`

The product's whole reason to exist. Both endpoints authenticate with
`SDKKeyAuthentication` and carry their own `ScopedRateThrottle` scope.

| Endpoint | Why / idea | Services & models |
| --- | --- | --- |
| `POST /sdk/evaluate/` | Answer "what value does *this user* get for *this flag* in *this environment*?" The environment and project are derived from the key itself, so callers never pass `env_id`. Returns typed `{result, result_type}` and fires the impression log asynchronously. Archived/missing flag → 404. Scope `evaluation`, default 1000/min. | `SDKKeyAuthentication` → `FlagEvaluationService.evaluate` → Redis, `EnvironmentFlag`, `Variation`, `RuleEvaluator`, `SegmentEvaluator`; `log_evaluation.delay(...)` → `EvaluationLog` |
| `POST /sdk/flags/evaluate/` | Client bootstrap: every flag in the key's environment resolved for one user context, in one call — what an SDK asks for at session start instead of N requests. Returns `{environment, flags: {key: {result, result_type, variation_id}}}`. Archived flags and flags not configured in this environment are omitted, not errors; an empty environment is `200` with `{}`. Scope `evaluation_bulk`, default 120/min, because one call does the work of N. Logs **no** impressions — a bootstrap is a download, not a read. | `SDKKeyAuthentication` → `FlagEvaluationService.evaluate_all` → `EvaluationQuery.active_flag_keys` / `get_active_env_flags`, Redis `get_many`/`set_many`. No Celery dispatch. |

**Why POST for a read?** The user context is an arbitrary nested object.
Query-string encoding it is lossy for anything but flat strings, and it would put
user attributes into access logs and proxy caches.

**What bulk evaluation costs.** A fixed number of round trips regardless of flag count:
one indexed query for the environment's flag keys (unavoidable — a warm cache knows
each flag's config, not which flags exist), one Redis `get_many`, and on misses only,
one query for those flags plus one for the union of segments they reference, then a
single `set_many`. The resolved payloads are passed into each evaluation as
`_preloaded`, so per-flag evaluation and prerequisite resolution touch neither Redis
nor the database again. Measured flat at 9 queries for 1 through 50 flags cold, and
exactly 1 warm; `TestBulkEvaluateCost` pins all of it.

**One engine, not two.** `evaluate_all` calls the same `evaluate` the per-flag endpoint
does, and `_build_flag_data` is shared by both paths so they can never write differently
shaped entries into the same cache. `TestBulkAgreesWithSingleEvaluate` parametrises
every targeting layer over several users and asserts the two endpoints agree flag for flag.

**The evaluation algorithm** (`FlagEvaluationService.evaluate`), in order:

1. **Cache lookup** — `flags:{project_id}:{env_id}:{flag_key}`. On miss, load from
   PostgreSQL (joining env flag → flag → variations, rules, targets, prerequisites, and
   the segments those rules reference) and warm the cache (TTL 300s).
2. **Kill switch** — env `is_enabled` false → `off_variation`. Nothing overrides this.
3. **Prerequisites** — each gate's flag is evaluated recursively *for the same user*;
   any that fails to serve its required variation → `off_variation`. The resolution
   chain and a depth cap make a corrupted graph fail closed rather than recurse.
4. **Individual targets** — a `user_id` pinned on this flag serves that variation.
5. **Rules** in `priority` order — the first match **wins outright**. A matched rule's
   own `rollout_percentage` decides whether this user is in the served slice; if not,
   `off_variation` — evaluation does *not* fall through to a later rule. Bucketing here
   is salted with the rule id so two rules at the same percentage pick different slices.
6. **Percentage rollout** — `SHA-256(flag_key + user_id) % 100 < rollout_percentage`,
   with **no salt**. In-bucket → `fallthrough_variation`; out → `off_variation`.
7. Legacy fallback: a flag with no variations returns raw `true`/`false`.

### 5.9 Evaluation logs & audit

| Endpoint | Why / idea | Services & models |
| --- | --- | --- |
| `GET /evaluation/logs/` | Past evaluations — which user got which result, with their context. Written **asynchronously**, so reads may lag live traffic slightly. Scoped by project membership. | `EvaluationLog` |
| `GET /evaluation/logs/{id}/` | One evaluation record. | `EvaluationLog` |
| `GET /audit/` | Change history with before/after JSON snapshots. Scoped to your own entries. | `AuditQuery.list_for_user` |
| `GET /audit/{id}/` | One audit entry. | `AuditLog` |

> Actions recorded: `create`, `update`, `delete`, `archive`, `unarchive`, `toggle`,
> `rollback`. See §7 for what is *not* audited yet.

### 5.10 Health — `/healthz/` (unversioned, no auth)

| Endpoint | Why / idea | Services & models |
| --- | --- | --- |
| `GET /healthz/` | Liveness/readiness probe. Runs `SELECT 1` against PostgreSQL and a write/read sentinel against Redis. `200` if both pass, `503` otherwise. Auth intentionally off so probes always get through. | Direct DB cursor + Redis |

---

## 6. What is already built

**Tenancy & access** — organizations, projects, membership roles
(`viewer`/`member`/`admin`/`owner`); 404-not-403 for non-members; last-owner protection.

**Core engine** — flag CRUD keyed per project; deterministic SHA-256 rollout; nine
targeting operators evaluated by priority; Redis caching scoped per
`(project, env, key)`, invalidated on every relevant write; the full evaluation
algorithm with typed results.

**Targeting (Phase 2, complete)** — individual user targeting; reusable segments with
include/exclude/rule membership; rule-level percentage rollout; prerequisite flags with
two-layer cycle protection.

**Multivariate flags** — `boolean` vs `multivariate`; JSON-stored typed values;
off/fallthrough wiring; rule-level `serve_variation`; backwards-compatible fallback.

**Flag lifecycle** — archive/unarchive; archived flags hidden from lists and 404'd from
evaluation; 409 on mutating an archived flag.

**Version history** — immutable per-change snapshots; append-only rollback that drops
dangling variation references.

**Multi-environment** — per-env state, env-scoped cache, the one-call `toggle` shortcut,
cascade delete.

**SDK keys** — server/client keys scoped to one environment; hash-only storage with a
once-shown raw key; revoke; atomic rotate; `last_used_at`.

**Security** — JWT for the dashboard; SDK-key auth where the key is the principal;
membership scoping everywhere; three-layer numeric validation; a dedicated rate limit on
evaluate.

**Observability** — audit trail with before/after snapshots; async evaluation logging via
Celery with retry/back-off; read-only audit and eval-log APIs.

**Infra** — `/healthz` DB+Redis probe; env-var config; `CONN_MAX_AGE`; compound indexes;
Docker Compose; Postman collection.

**359 tests** covering all of the above.

---

## 7. Known rough edges

- **No self-serve registration.** Users come from `createsuperuser` or the admin. A
  `POST /api/v1/auth/register/` should also provision a personal organization and
  default project, mirroring the `organizations/0002_backfill_personal_orgs` migration.
- **Partial audit coverage.** Flag, variation, environment, segment, target, and
  prerequisite mutations are audited. **Rule, SDK key, and organization mutations are
  not** — `AuditService` is only called from `flags/`, `segments/`, and `environment/`.
- **Model/migration drift.** `makemigrations --check` still reports pending
  `Alter field id` changes on `evaluation` and `sdk_keys`.
- **`FeatureFlag.is_enabled` / `rollout_percentage` are quasi-legacy.** Evaluation reads
  the `EnvironmentFlag` values; these serve as global defaults.
- **Prerequisite chains cost a cache read each.** One evaluation resolves one cached
  entry per flag in the chain — no DB, but not free for deep chains.
- **`delete_segment` check-then-delete is not transactional.** A rule created
  concurrently with a segment delete could leave a dangling reference. Benign, because
  unknown segments fail closed — do *not* "fix" it by inverting that fallback.
- **`targeting` app has leftover scaffold models** (`Country`, `City`).
- **Compose stores no data** — neither `db` nor `redis` declares a volume.

---

## 8. What remains (roadmap)

**Phase 1 — foundational data model** — ✅ complete

**Phase 2 — targeting power** — ✅ complete

- [x] Individual user targeting
- [x] Reusable segments
- [x] Rule-level percentage rollout
- [x] Prerequisite flags

**Phase 3 — real-time SDK infra** — in progress

- [x] SDK **client bootstrap** — `POST /sdk/flags/evaluate/` (one user context, every flag)
- [ ] SDK **config download** — `GET /sdk/flags/config/` (raw ruleset, server-side SDKs evaluate
      in-process). Specified in `SDK_CONFIG_SPEC.md`; blocked on the `gt`/`lt` crash in §9.1
- [ ] Impression **batching** endpoint (bulk eval-log ingest from an SDK)
- [ ] **SSE streaming** — push flag updates to connected SDKs (builds on `config_version`)

**Phase 4 — workflow & governance**

- [ ] Stale-flag detection (Celery-beat job)
- [ ] Scheduled flag changes
- [ ] Webhook notifications on mutations
- [ ] Approval workflows for production changes

**Phase 5 — observability & analytics**

- [ ] Impression aggregation (hourly rollup + stats endpoint)
- [ ] Data export to S3 / BigQuery

**Phase 6 — experimentation**

- [ ] A/B testing framework
- [ ] Statistical significance reporting

**Phase 7 — enterprise**

- [ ] SSO + SCIM provisioning

### The single most impactful next endpoint

`POST /api/v1/auth/register/`. For a backend meant to be adopted "in minutes", the fact
that the only way to create a user is `createsuperuser` is the sharpest edge left.

---

## 9. Endpoint index (quick reference)

```text
# Auth
POST   /api/v1/auth/token/
POST   /api/v1/auth/token/refresh/

# Organizations & projects
GET    /api/v1/organizations/
POST   /api/v1/organizations/
GET    /api/v1/organizations/{slug}/
DELETE /api/v1/organizations/{slug}/                        (owner)
GET    /api/v1/organizations/{slug}/members/
POST   /api/v1/organizations/{slug}/members/                (admin+)
PATCH  /api/v1/organizations/{slug}/members/{user_id}/      (admin+)
DELETE /api/v1/organizations/{slug}/members/{user_id}/      (admin+)
GET    /api/v1/projects/
POST   /api/v1/projects/                                    (admin+)
GET    /api/v1/projects/{key}/
DELETE /api/v1/projects/{key}/                              (admin+)

# Flags  (under /api/v1/projects/{project_key}/)
GET    flags/                                               (?include_archived=true)
POST   flags/
GET    flags/{key}/
PATCH  flags/{key}/                                         (409 archived, 400 key change)
DELETE flags/{key}/                                         (409 if it gates another flag)
POST   flags/{key}/archive/
POST   flags/{key}/unarchive/
POST   flags/{key}/toggle/                                  body: {"environment": "..."}
GET    flags/{key}/variations/
POST   flags/{key}/variations/
PATCH  flags/{key}/variations/{id}/
DELETE flags/{key}/variations/{id}/
GET    flags/{key}/targets/
PUT    flags/{key}/targets/                                 201 create / 200 update
DELETE flags/{key}/targets/{user_key}/
GET    flags/{key}/prerequisites/
PUT    flags/{key}/prerequisites/                           409 on a cycle
DELETE flags/{key}/prerequisites/{prerequisite_key}/
GET    flags/{key}/versions/
GET    flags/{key}/versions/{n}/
POST   flags/{key}/versions/{n}/rollback/

# Segments  (under /api/v1/projects/{project_key}/)
GET    segments/
POST   segments/
GET    segments/{key}/
PATCH  segments/{key}/
DELETE segments/{key}/                                      (409 if referenced)
GET    segments/{key}/targets/
PUT    segments/{key}/targets/
DELETE segments/{key}/targets/{user_key}/
GET    segments/{key}/rules/
POST   segments/{key}/rules/
PATCH  segments/{key}/rules/{id}/
DELETE segments/{key}/rules/{id}/

# Environments  (under /api/v1/projects/{project_key}/)
GET    environments/
POST   environments/
GET    environments/{id}/
DELETE environments/{id}/
GET    environments/{id}/flags/
PATCH  environments/{id}/flags/{flag_id}/

# Rules
GET    /api/v1/rules/
POST   /api/v1/rules/
GET    /api/v1/rules/{id}/
PATCH  /api/v1/rules/{id}/
DELETE /api/v1/rules/{id}/

# SDK keys
POST   /api/v1/sdk-keys/
GET    /api/v1/sdk-keys/
GET    /api/v1/sdk-keys/{id}/
POST   /api/v1/sdk-keys/{id}/revoke/
POST   /api/v1/sdk-keys/{id}/rotate/

# SDK
POST   /api/v1/sdk/evaluate/                                header: X-SDK-Key
POST   /api/v1/sdk/flags/evaluate/                          header: X-SDK-Key

# Observability
GET    /api/v1/evaluation/logs/
GET    /api/v1/evaluation/logs/{id}/
GET    /api/v1/audit/
GET    /api/v1/audit/{id}/
GET    /healthz/
```
