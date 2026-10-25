# Recipes

Curl recipes against a local stack (`docker compose up`). `<token>` is a JWT access token
from registration; `sdk_srv_<token>` is a raw SDK key.

## Get a working account

```bash
curl -X POST http://localhost:8000/api/v1/auth/register/ \
  -H "Content-Type: application/json" \
  -d '{"username": "you", "email": "you@example.com", "password": "s3cur3-passphrase!"}'
```

One call. The response carries a JWT pair, plus the organization, the `Default` project and
the three environments it provisioned. A bare user could not do anything, since flags
belong to a project and a project only exists inside an organization.

`createsuperuser` is still how you get an account for the Django admin at `/admin/`.

## Add an environment and set a flag's state in it

Environments are project-scoped, so this needs no code change:

```bash
curl -X POST http://localhost:8000/api/v1/projects/{project_key}/environments/ \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"name": "staging"}'

curl -X PATCH http://localhost:8000/api/v1/projects/{project_key}/environments/{env_id}/flags/{flag_id}/ \
  -H "Authorization: Bearer <token>" \
  -d '{"is_enabled": true, "rollout_percentage": 50}'
```

Or flip the kill switch in one call:

```bash
curl -X POST http://localhost:8000/api/v1/projects/{project_key}/flags/{key}/toggle/ \
  -H "Authorization: Bearer <token>" \
  -d '{"environment": "production"}'
```

## Issue and use an SDK key

```bash
# Create a server key for an environment. The response includes the full key exactly
# once: save it immediately.
curl -X POST http://localhost:8000/api/v1/sdk-keys/ \
  -H "Authorization: Bearer <token>" \
  -d '{"name": "Prod Server", "key_type": "server", "environment": 1}'

# Evaluate one flag
curl -X POST http://localhost:8000/api/v1/sdk/evaluate/ \
  -H "X-SDK-Key: sdk_srv_<token>" \
  -d '{"flag_key": "dark-mode", "user_context": {"user_id": "u_1", "plan": "pro"}}'

# Bootstrap a session: every flag, one user context
curl -X POST http://localhost:8000/api/v1/sdk/flags/evaluate/ \
  -H "X-SDK-Key: sdk_srv_<token>" \
  -d '{"user_context": {"user_id": "u_1", "plan": "pro"}}'
```

## Rotate or revoke an SDK key

```bash
# Rotate: revokes the old key and issues a new one, returning the new full key once
curl -X POST http://localhost:8000/api/v1/sdk-keys/{id}/rotate/ \
  -H "Authorization: Bearer <token>"

# Revoke only
curl -X POST http://localhost:8000/api/v1/sdk-keys/{id}/revoke/ \
  -H "Authorization: Bearer <token>"
```

Both are audited, as `rotate` and `revoke` respectively.

## Download the config for in-process evaluation

```bash
# Server keys only. A client key gets 403, because the payload holds user identifiers.
curl -i http://localhost:8000/api/v1/sdk/flags/config/ \
  -H "X-SDK-Key: sdk_srv_<token>"

# Poll: returns 304 with an empty body until something actually changes.
curl -i http://localhost:8000/api/v1/sdk/flags/config/ \
  -H "X-SDK-Key: sdk_srv_<token>" \
  -H 'If-None-Match: "4127"'
```

## Report impressions from a local SDK

Flags evaluated in-process never touch `/sdk/evaluate/`, so this endpoint is the only way
they reach `EvaluationLog`.

```bash
curl -X POST http://localhost:8000/api/v1/sdk/impressions/ \
  -H "X-SDK-Key: sdk_srv_<token>" \
  -H "Content-Type: application/json" \
  -d '{"impressions": [
        {"flag_key": "dark-mode", "result": true, "user_context": {"user_id": "u_1"}}
      ]}'
# → 202 {"accepted": 1, "dropped": []}
```

Unknown keys come back in `dropped` rather than failing the batch. The status is `202`
because a Celery worker writes the rows.

## Archive and restore a flag

```bash
curl -X POST http://localhost:8000/api/v1/projects/{project_key}/flags/{key}/archive/ \
  -H "Authorization: Bearer <token>"

curl -X POST http://localhost:8000/api/v1/projects/{project_key}/flags/{key}/unarchive/ \
  -H "Authorization: Bearer <token>"
```

## Inspect the cache

Redis requires the password from `.env`, and Django prefixes every key with `:1:`. Values
are pickled, except the generation, which is a plain integer.

```bash
export $(grep '^REDIS_PASSWORD=' .env)
docker compose exec -e REDISCLI_AUTH="$REDIS_PASSWORD" redis redis-cli -n 1

SCAN 0 MATCH ':1:flags:*' COUNT 1000      # per-flag payloads: :1:flags:{project}:{env}:{generation}:{key}
SCAN 0 MATCH ':1:config:*' COUNT 1000     # whole config-download payloads, keyed by config_version
GET ':1:flaggen:<project_id>:<env_id>'    # the environment's current cache generation
```

Don't `DEL` keys to force a refresh. That doesn't bump `config_version`, so polling SDKs
keep their copy. Invalidate through the engine instead, which advances the generation and
then bumps the version:

```bash
docker compose run --rm web python manage.py shell -c \
  "from apps.flags.models import FeatureFlag; from apps.flags.services import FlagService; \
   FlagService.invalidate_flag_caches(FeatureFlag.objects.get(project__key='<project_key>', key='<flag_key>'))"
```

## Watch the Celery worker

```bash
docker compose logs -f celery
```

## Health check

```bash
curl http://localhost:8000/healthz/
# 200 → PostgreSQL + Redis both healthy
# 503 → at least one dependency is down
```
