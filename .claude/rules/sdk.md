---
paths:
  - "apps/sdk/**"
  - "apps/sdk_keys/**"
  - "apps/environment/services.py"
---

# SDK endpoints and keys

Endpoints authenticate with `X-SDK-Key` and reject JWTs. The key **is** the principal:
there is no user behind an SDK request.

## Keys

- Server keys (`sdk_srv_`) and client keys (`sdk_cli_`) are each scoped to one environment.
- Only the SHA-256 hash is stored. A 16-char prefix is kept for display, and the full key
  is returned exactly once. Rotation is atomic.
- Rotate and revoke are audited as `rotate` and `revoke`, because they are different events
  to whoever reads the trail after an incident.

## The two bulk endpoints are not alternatives

- `POST /sdk/flags/evaluate/` (bootstrap) costs one round trip per *user context*. That is
  right for a browser SDK, with one user per session.
- `GET /sdk/flags/config/` (raw ruleset, `config_version` ETag, `304` on an unchanged poll)
  is for server-side SDKs evaluating thousands of users per process. It is **server keys
  only**: a client key gets 403, because the payload holds user identifiers. The contract
  is `SDK_CONFIG_SPEC.md`.

## Client keys see only client-side flags

A `sdk_cli_` key ships to browsers, so it is public. It reaches only flags with
`client_side_available=True`.

- A hidden flag returns a 404 **identical to a missing key**. A different body would
  confirm the flag exists.
- New flags default to hidden. Migration `flags.0014` marked pre-existing flags visible, so
  live browser SDKs kept working.
- How the engine filters is in [evaluation.md](evaluation.md) → `client_side_only`.

## Secure mode

`Environment.secure_mode` makes a client key prove its backend vouched for the user:
`hash = HMAC-SHA256(secure_mode_secret, str(user_id))`, hex, on `POST /sdk/evaluate/` and
`POST /sdk/flags/evaluate/`. `SecureModeService.assert_vouched`
(`apps/environment/services.py`) enforces it. It is a no-op for server keys and for
environments not in secure mode, and both SDK views call it before evaluating.

- The secret is **stored raw**, because the server recomputes the HMAC, so hash-only
  storage cannot apply. That is why it is in `AuditService.REDACTED_FIELDS`, never in
  `EnvironmentSerializer`, and readable only at MEMBER+.
- Compare with `hmac.compare_digest` on **bytes**. On `str` it raises for non-ASCII input,
  and the hash is caller-supplied.
- Re-enabling secure mode keeps the secret; only `rotate` replaces it. Toggling must not
  break every hash a backend has already issued.
- It binds `user_id` only, as LaunchDarkly's does. Other attributes remain caller-supplied,
  so do not describe it as protecting attribute rules.
- Impressions are not checked: they write logs and return no evaluation data.

## Impressions are reads, not downloads

- `POST /sdk/impressions/` is where locally evaluated flags reach `EvaluationLog`. Its
  `user_context` is per impression, never per batch. A server SDK's flush spans many users,
  and a shared context would force one request each.
- Unknown flag keys are dropped and named in `dropped`. Never make one stale key reject a
  batch, or an SDK holding a slightly old config can never flush again. It returns `202`,
  because a Celery worker writes the rows.
- `POST /sdk/flags/evaluate/` resolves every flag in an environment and logs **none** of
  them. A bootstrap is a download: the app may go on to read three of fifty flags, and
  `EvaluationLog` has no rollup, so logging all fifty records fetches nobody consumed. Do
  not "restore" logging there. `TestBulkImpressionLogging` fails if a task is dispatched or
  a row is written.
- `POST /sdk/evaluate/` still logs, because it genuinely serves one flag to one caller.
- Archived flags return 404 from every SDK endpoint.
