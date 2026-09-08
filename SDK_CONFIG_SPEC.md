# SDK Config Download — Specification

**Status:** Implemented. Phase 3, item 1b.
**Endpoint:** `GET /api/v1/sdk/flags/config/`
**Supersedes nothing** — it sits alongside `POST /api/v1/sdk/flags/evaluate/`.

---

## 1. Why this exists

The engine ships two bulk endpoints because there are two kinds of SDK, and they
have opposite cost profiles.

| | `POST /sdk/flags/evaluate/` (built) | `GET /sdk/flags/config/` (this spec) |
|---|---|---|
| Returns | Flags resolved for **one** user context | The environment's **ruleset**, unevaluated |
| Evaluation runs | On the server | In the SDK, in-process |
| Round trips scale with | Number of **user contexts** | Number of **config changes** |
| Latency per flag check | One network hop (~1–50ms) | In-process (~1µs) |
| Fits | Browser / client-side SDK: one user per session | Server-side SDK: thousands of users per process |
| Key types accepted | `sdk_srv_` and `sdk_cli_` | **`sdk_srv_` only** — see §7 |

A server-side SDK calls `variation(flag, user)` inside *its own* application's
request path. Putting a network hop there is the thing this endpoint removes: the
SDK downloads once at process start, evaluates locally, and refreshes when the
config changes (polling now, SSE in Phase 3 item 3).

**The risk this creates, stated plainly.** Local evaluation means every SDK
reimplements bucketing, rule precedence, segment membership, and prerequisite
resolution. Any divergence serves the wrong value to real users, silently. §6 is
the mitigation and it is not optional.

---

## 2. Request

```http
GET /api/v1/sdk/flags/config/
X-SDK-Key: sdk_srv_<token>
If-None-Match: "4127"
```

The environment and project are derived from the key, as with every SDK endpoint.
No request body, no query parameters.

**Responses**

| Code | When |
|---|---|
| `200` | Config returned. `ETag` header carries the config version. |
| `304` | `If-None-Match` matches the current version. Empty body. |
| `401` | Missing, invalid, or revoked key. |
| `403` | Valid **client** key (`sdk_cli_`). See §7. |

`304` is the common case: a polling SDK asks every 30s and gets an empty response
until something actually changes.

---

## 3. Wire format

```json
{
  "format_version": 1,
  "environment": "production",
  "config_version": 4127,
  "segments": {
    "beta": {
      "included": ["alice", "bob"],
      "excluded": ["carol"],
      "rules": [ { "attribute": "plan", "operator": "eq", "value": "pro" } ]
    }
  },
  "flags": {
    "dark-mode": {
      "flag_type": "boolean",
      "is_enabled": true,
      "rollout_percentage": 20,
      "off_variation":         { "id": 18, "value": false, "value_type": "boolean" },
      "fallthrough_variation": { "id": 17, "value": true,  "value_type": "boolean" },
      "targets": { "alice": 17, "carol": 18 },
      "prerequisites": [ { "flag_key": "gate", "required_variation_id": 9 } ],
      "rules": [
        {
          "id": 55,
          "attribute": "country",
          "operator": "eq",
          "value": "EG",
          "priority": 1,
          "rollout_percentage": 50,
          "serve_variation": { "id": 17, "value": true, "value_type": "boolean" }
        }
      ]
    }
  }
}
```

### Differences from the internal cached payload — all deliberate

This is a **public wire format**, not a dump of `flag_data`. Four changes:

1. **Segments are normalized to a top-level map.** The cache stores each flag's
   own segment slice, which is right there (entries stay independent). Duplicating
   a 50,000-member segment across every flag that references it is not right in a
   download. Rules reference segments by key; the SDK resolves against the shared map.
2. **Sets become arrays.** `SegmentQuery.evaluation_payload` builds Python `set`s
   for `included`/`excluded`. `json.dumps` cannot encode a set. Conversion happens
   at the serializer boundary — the same class of hazard as the Celery boundary
   (see AGENTS.md → Celery boundary).
3. **`targets` maps `user_key → variation_id`**, not to a full variation dict. The
   variations are already in the payload; repeating them per target is waste.
4. **`format_version` is explicit and independent of `config_version`.** The former
   versions the *shape*; the latter versions the *contents*. An SDK refuses a
   `format_version` it does not know rather than guessing.

### Excluded from the response

Archived flags; flags with no `EnvironmentFlag` row in this environment; any flag
or segment belonging to another project. Same scoping rule as every other SDK endpoint.

---

## 4. The evaluation contract

An SDK consuming this format **must** implement exactly the following. This is a
restatement of `FlagEvaluationService.evaluate` and is normative.

### 4.1 Precedence — each step short-circuits

1. **Kill switch.** `is_enabled == false` → serve `off_variation`. Nothing below
   can override this, individual targets included.
2. **Prerequisites.** Every entry in `prerequisites` must resolve — *for this same
   user context* — to a variation whose `id` equals `required_variation_id`.
   Comparison is by **variation id, never by value**: two variations of one flag
   may carry the same value. Any unmet gate → `off_variation`.
3. **Individual targets.** If `targets[user_id]` exists, serve that variation id.
4. **Rules**, ascending by `priority`. The first rule that matches **wins
   outright** — evaluation never falls through to a later rule. If the matched
   rule has `rollout_percentage < 100` and this user is outside its slice, serve
   `off_variation`; do **not** continue to the next rule.
5. **Percentage rollout.** In bucket → `fallthrough_variation`; outside → `off_variation`.

A flag with `off_variation`/`fallthrough_variation` of `null` (legacy boolean
flags with no variations configured) serves raw `false`/`true` respectively.

### 4.2 Bucketing — byte-exact, no latitude

```
flag level:  SHA256(  ""                    + flag_key + user_id )  % 100 < rollout_percentage
rule level:  SHA256( "rule:" + rule_id + ":" + flag_key + user_id )  % 100 < rollout_percentage
```

The hash digest is read as a **hexadecimal integer** (`int(hexdigest, 16)`), not
as bytes. `user_id` is `str(user_context["user_id"])`, empty string if absent.
`rollout_percentage <= 0` is always false and `>= 100` always true, short-circuited
before hashing.

**The flag-level salt is empty and must stay empty forever.** That hash decides
which users already have a flag. Changing any of its inputs re-buckets every user
and flips live flags on deploy.

**The rule-level salt is the rule id**, so two rules at the same percentage select
different slices instead of the same users.

### 4.3 Operators

`attribute` is looked up in the user context. **A missing attribute never matches**,
whatever the operator. The context value is stringified (`str(value)`) before
comparison, except `gt`/`lt`.

| Operator | Semantics |
|---|---|
| `eq` / `neq` | String equality against `value` |
| `contains` | `value` is a substring of the context value |
| `in` / `not_in` | `value` is split on `,` and each part stripped; membership test |
| `gt` / `lt` | Both sides coerced to float; **an operand that will not coerce does not match** — see §4.5 |
| `in_segment` / `not_in_segment` | `attribute` is ignored; `value` is a segment key |

### 4.4 Segment membership — precedence, highest first

1. In `excluded` → **out** (an exclusion beats a rule the user matches).
2. In `included` → **in**.
3. **Any** rule matches → in (OR, not AND).
4. Otherwise out.

A segment with no targets and no rules matches **nobody**, never everybody.
Segments do not nest: a segment rule carrying `in_segment`/`not_in_segment` is
invalid and must be **ignored**, not evaluated.

### 4.5 Fail closed — the rules that invert

Everything unresolvable resolves to *off*. Never invert an unresolvable reference.

- A rule naming a **segment key absent from the map does not match — whatever the
  operator.** Not `not(unresolvable)`. Inverting it would make `not_in_segment`
  match every user and turn one dangling reference into a full rollout.
- A `gt`/`lt` operand that **will not coerce to a number does not match**, on either
  side. `{"age": "unknown"}` against `age gt 18` is not an error and not a match.
  Neither operator may be implemented as the negation of the other: both must
  return false, or an unusable attribute matches half the users it touches.
- A prerequisite naming a **flag absent from the payload** leaves the dependent off.
- A **prerequisite cycle** leaves every flag in it off. SDKs must carry the chain
  of flags being resolved and bail on a repeat, with a depth cap of **10**
  (`MAX_PREREQUISITE_DEPTH`). The server rejects cycles at write time, so this is
  the defensive layer — but without it a corrupted graph is unbounded recursion
  inside the customer's process.

---

## 5. Config version

`config_version` is a monotonically increasing integer per environment, carried in
the body and as the `ETag`.

**Proposal:** add `Environment.config_version` (`PositiveBigIntegerField`,
default 1) and bump it with an `F("config_version") + 1` update at the same call
sites that already evict flag caches — `FlagService.invalidate_flag_caches` and
`invalidate_many_flag_caches`. Those sites are already the chokepoint for "something
changed that affects what a flag serves", so this adds no new invalidation surface.

An atomic `F()` update avoids the read-modify-write race that a Python-side
increment would have under concurrent writes.

This is what makes SSE (item 3) tractable: a stream event carries the new
`config_version`, and an SDK that sees a gap does a full re-fetch instead of
trusting a delta it cannot verify.

---

## 6. Conformance vectors — how divergence is prevented

**This is the load-bearing part of the spec.** Without it, "every SDK
reimplements the engine" is exactly the failure mode that made me build the
evaluated endpoint first.

Ship a generated fixture, `sdk_conformance_vectors.json`:

```json
{
  "format_version": 1,
  "config": { ...a config payload exercising every operator, segment shape,
              prerequisite chain, and rollout boundary... },
  "cases": [
    { "user_context": {"user_id": "alice", "plan": "pro"},
      "expect": {"dark-mode": {"result": true, "variation_id": 17}} }
  ]
}
```

Rules that make it work:

- **Generated by the server's own engine**, never hand-written. A management
  command builds the config, runs `FlagEvaluationService` over every case, and
  writes the expectations. The vectors cannot drift from the implementation
  because the implementation produces them.
- **A CI test regenerates and diffs.** If engine behavior changes, the vectors
  change, and the diff is the reviewable record that an SDK contract moved.
- **Bucketing gets dense coverage.** At minimum: several thousand user ids at
  a fixed percentage, asserting the exact in/out partition. An SDK that reads the
  digest as bytes instead of a hex integer, or that salts the flag-level rollout,
  fails immediately rather than at 3% of production traffic.
- **Every fail-closed case is a vector**: dangling segment under both operators,
  missing prerequisite, cyclic prerequisite, unconfigured segment, missing
  attribute, and a non-numeric `gt`/`lt` operand under both operators.

An SDK is conformant when it passes the vectors. That is the answer to the
objection that made this endpoint risky.

---

## 7. Security

**Server keys only. Client keys get `403`.**

This is the sharpest difference from every other SDK endpoint, which accept both
key types. The config payload contains `targets` (individual `user_key`s) and
segment `included`/`excluded` lists. In practice those hold real identifiers —
emails, account ids, internal user ids. `sdk_cli_` keys are shipped to browsers
and are readable by anyone who opens devtools.

Serving this payload to a client key would publish the customer's user list. The
existing `HasSDKKey` permission is not sufficient; this endpoint needs a
`HasServerSDKKey` permission that checks `key_type`.

A follow-up worth considering: a `client-visible` flag on `Segment` and
`FlagTarget`, plus a filtered client config. Out of scope here — the `403` is the
correct default until someone needs it.

---

## 8. How it is built

Follows the existing four-layer split; no new app.

| Layer | Change |
|---|---|
| `apps/environment/models.py` | `Environment.config_version` + migration |
| `apps/environment/queries.py` | `EnvironmentQuery.bump_config_versions(env_ids)` — one `F()` UPDATE for the whole set |
| `apps/flags/services.py` | `_invalidate_env_caches` evicts *and* bumps; `_evict_env_caches` split out so a fan-out bumps once, not once per flag |
| `apps/environment/services.py` | `EnvironmentFlagService._invalidate_cache` routed through the same pair instead of formatting the cache key itself |
| `apps/evaluation/services.py` | `FlagEvaluationService.config_for(...)` — normalize segments, sets → arrays, targets → id map |
| `apps/sdk_keys/permissions.py` | `HasServerSDKKey` |
| `apps/sdk/serializers.py` | `SDKConfigResponseSerializer` |
| `apps/sdk/views.py` | `SDKConfigView` — `GET`, ETag / `If-None-Match` (weak validators and lists included), own throttle scope `config_download` |
| `apps/sdk/urls.py` | `path("flags/config/", ...)` |
| `apps/evaluation/conformance.py` | The fixture and the vector generator |
| management command | `generate_conformance_vectors` (`--check` runs in CI) |

**No dedicated query.** `config_for` builds the payload from the same
`_preload_flag_data` map that `evaluate_all` resolves, rather than from a query
of its own. That is the point, and it is stronger than the original sketch's
`EvaluationQuery.config_payload`: the config an SDK evaluates locally and the
config the server evaluates centrally are literally the same dicts, so they
cannot drift apart through a second query that fetched slightly different rows.
It also means the download reuses the warm flag cache instead of competing with it.

**One bump site, not two.** The sketch said "the two `invalidate_*_caches`
methods". There were three places that evicted a flag cache — the third was
`EnvironmentFlagService`, formatting the cache key by hand. All of them now
funnel through `_invalidate_env_caches`, which evicts and bumps together. Keeping
them together is the whole contract: an SDK polling a version that never moves
while the server serves something new is worse than no versioning at all.

**Caching.** The whole serialized payload is cached under
`config:{project_id}:{env_id}:{config_version}`. Keying by version makes it
self-invalidating: a bump changes the key, and the old entry expires on its own.
No new invalidation call sites.

**Response size.** Uncapped, deliberately — see §9.2.

---

## 9. Open questions — must be settled before `format_version: 1` freezes

### 9.1 `gt` / `lt` against a non-numeric attribute — **settled 2026-08-29**

Resolved as **fail closed**: an operand that will not coerce to a number does not
match, on either side, for both operators. This is the same answer the engine
gives a missing attribute or a dangling segment key, and it is now part of the
conformance contract (§4.3, §4.5, and the `gt`/`lt` vectors in §6).

It was previously an uncaught `ValueError` — a **500 on `POST /sdk/evaluate/`**.
Returning `400` at evaluation time was rejected: it turns one bad user attribute
into a failed request inside the customer's hot path. Coercing at rule-write time
does not close the hole on its own (the *context* arrives at runtime), but the
server does it as well, so a non-numeric rule `value` is a 400 at authoring time
rather than a rule that silently matches nobody. SDKs need not replicate that
check — they never see an unvalidated rule.

### 9.2 Response size ceiling — **settled 2026-09-08**

Resolved as **no ceiling, and never a silent exclusion.**

Dropping an oversized segment from the payload was the tempting option and is
the wrong one. A rule naming a segment the SDK cannot find fails closed (§4.5),
so excluding a 50,000-member segment does not degrade gracefully — it silently
turns the flag off for everyone that segment was letting in. A size limit that
changes who gets a feature is not a size limit.

Refusing the whole request past a ceiling was rejected too: it takes an
environment that works today and breaks it on the request *after* someone adds
a target, with no way for the SDK to recover on its own.

So the payload is served whole, and the mitigations are operational:

- **Compression belongs at the reverse proxy**, not in Django. Django's
  `GZipMiddleware` is global, and this API has responses that reflect
  caller-supplied input next to a credential — `POST /auth/register/` echoes the
  submitted username in a body that also carries a JWT pair, which is the exact
  BREACH shape. Compressing that to save bytes on a different endpoint is a bad
  trade. Configure gzip for `/api/v1/sdk/flags/config/` at the edge.
- **A large payload is a signal, not a failure.** An environment whose config
  runs to megabytes is one where segment membership is being used as a user
  database. The `client-visible` follow-up in §7, or evaluating server-side via
  `POST /sdk/flags/evaluate/`, is the answer there — not a truncated ruleset.

### 9.3 Does the config download log anything?

Recommended: **no**. It is a config fetch, not an evaluation — nothing has been
served to anyone yet. Impressions for locally-evaluated flags arrive through the
batching endpoint (Phase 3, item 2). This is the same reasoning that removed
impression logging from the bootstrap endpoint.

### 9.4 Polling interval guidance — **settled 2026-09-08**

**30 seconds**, advertised by the endpoint itself as
`Cache-Control: max-age=30, private` on both the `200` and the `304`
(`SDKConfigView.POLL_INTERVAL_SECONDS`). Advisory — the `304` is what makes
polling cheap, not the header — but it means every SDK reads the interval off
the wire instead of picking one.

`private` because the payload is scoped to a single key's environment and must
never be held in a shared cache.
