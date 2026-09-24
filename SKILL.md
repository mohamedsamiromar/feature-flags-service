# SKILL.md — Feature Flag Engine

Step-by-step workflows for common development tasks on this project.

---

## Getting a Working Account

```bash
curl -X POST http://localhost:8000/api/v1/auth/register/ \
  -H "Content-Type: application/json" \
  -d '{"username": "you", "email": "you@example.com", "password": "s3cur3-passphrase!"}'
```

One call. The response carries a JWT pair plus the organization, `Default` project, and
the three environments it provisioned — a bare user could not do anything, since flags
belong to a project and a project only exists inside an organization.

`createsuperuser` is still how you get an account for the Django admin at `/admin/`.

## Adding a New Flag Feature (e.g. a new action endpoint)

1. Add the action to `FlagService` in [apps/flags/services.py](apps/flags/services.py).
2. Register the `@action` on `FlagViewSet` in [apps/flags/views.py](apps/flags/views.py).
3. If the action mutates the flag, call `AuditService.log(...)` **and**
   `FlagService.invalidate_flag_caches(...)` inside the service method. The second one
   also advances `Environment.config_version`, which is the config download's ETag.
4. Write tests under [apps/flags/tests/](apps/flags/tests/).

## Adding a New Targeting Operator

1. Add the operator constant to `Operator` in [apps/rules/models.py](apps/rules/models.py).
2. Implement the match branch in `RuleEvaluator._evaluate` in
   [apps/targeting/services.py](apps/targeting/services.py). **Decide what a failed
   coercion means before you write it** — the engine's answer everywhere else is "does
   not match", and it must never be expressible as the negation of another operator.
3. If it needs write-time validation (as `gt`/`lt` do), add it to *both*
   `RuleService` and `SegmentService`.
4. Add it to the conformance fixture in
   [apps/evaluation/conformance.py](apps/evaluation/conformance.py), with a user context
   that matches and one that does not.
5. Regenerate the vectors and review the diff:
   ```bash
   docker compose exec web python manage.py generate_conformance_vectors
   ```
   `test_every_operator_is_exercised` fails if you skip step 4.
6. Add match/non-match test cases under
   [apps/targeting/tests/](apps/targeting/tests/).

## Adding a New Environment

Via the API (no code change required — environments are project-scoped):

```bash
curl -X POST http://localhost:8000/api/v1/projects/{project_key}/environments/ \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"name": "staging"}'
```

Then set a flag's state in it:

```bash
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

## Issuing and Using an SDK Key

```bash
# Create a server key for an environment
curl -X POST http://localhost:8000/api/v1/sdk-keys/ \
  -H "Authorization: Bearer <token>" \
  -d '{"name": "Prod Server", "key_type": "server", "environment": 1}'
# Response includes the full key exactly once — save it immediately.

# Evaluate one flag
curl -X POST http://localhost:8000/api/v1/sdk/evaluate/ \
  -H "X-SDK-Key: sdk_srv_<token>" \
  -d '{"flag_key": "dark-mode", "user_context": {"user_id": "u_1", "plan": "pro"}}'

# Bootstrap a session: every flag, one user context
curl -X POST http://localhost:8000/api/v1/sdk/flags/evaluate/ \
  -H "X-SDK-Key: sdk_srv_<token>" \
  -d '{"user_context": {"user_id": "u_1", "plan": "pro"}}'
```

## Downloading the Config for In-Process Evaluation

```bash
# Server keys only — a client key gets 403, because the payload holds user identifiers.
curl -i http://localhost:8000/api/v1/sdk/flags/config/ \
  -H "X-SDK-Key: sdk_srv_<token>"

# Poll: 304 with an empty body until something actually changes.
curl -i http://localhost:8000/api/v1/sdk/flags/config/ \
  -H "X-SDK-Key: sdk_srv_<token>" \
  -H 'If-None-Match: "4127"'
```

## Reporting Impressions from a Local SDK

Flags evaluated in-process never touch `/sdk/evaluate/`, so this is the only way they
reach `EvaluationLog`.

```bash
curl -X POST http://localhost:8000/api/v1/sdk/impressions/ \
  -H "X-SDK-Key: sdk_srv_<token>" \
  -H "Content-Type: application/json" \
  -d '{"impressions": [
        {"flag_key": "dark-mode", "result": true, "user_context": {"user_id": "u_1"}}
      ]}'
# → 202 {"accepted": 1, "dropped": []}
```

Unknown keys come back in `dropped` rather than failing the batch. `202` because a
Celery worker writes the rows.

## Running a Subset of Tests

```bash
# Flags app only
docker compose exec web pytest apps/flags/ -v

# SDK endpoints (evaluate, bootstrap, config download, impressions)
docker compose exec web pytest apps/sdk/tests/ -v

# The SDK contract: conformance vectors
docker compose exec web pytest apps/evaluation/tests/test_conformance_vectors.py -v

# A single test by name
docker compose exec web pytest apps/flags/ -k "test_archive" -v
```

## Creating a Migration

```bash
docker compose exec web python manage.py makemigrations <app_name>
docker compose exec web python manage.py migrate

# CI enforces that nothing is pending:
docker compose exec web python manage.py makemigrations --check --dry-run
```

## Changing a Celery Task

**Restart the worker.** Celery does not auto-reload, and the volume mount does not help.

```bash
docker compose restart celery
```

Skip it after a signature change and the web container queues the new shape while the
old worker rejects it — the endpoint still returns `202` and nothing is written. In a
real deploy, roll workers before web.

## Debugging Evaluation (Cache Inspection)

```bash
docker compose exec redis redis-cli -n 1

KEYS flags:*                              # per-flag evaluation payloads
KEYS config:*                             # whole config-download payloads
GET flags:<project_id>:<env_id>:<flag_key>
DEL flags:<project_id>:<env_id>:<flag_key>
```

`config:` entries are keyed by `config_version`, so they never need explicit
invalidation — a bump moves the key and the old entry expires on its own.

## Checking Celery Task Output

```bash
docker compose logs -f celery
```

## Health Check

```bash
curl http://localhost:8000/healthz/
# 200 → PostgreSQL + Redis both healthy
# 503 → at least one dependency is down
```

## Rotating or Revoking an SDK Key

```bash
# Rotate (revoke old, issue new — returns the new full key once)
curl -X POST http://localhost:8000/api/v1/sdk-keys/{id}/rotate/ \
  -H "Authorization: Bearer <token>"

# Revoke only
curl -X POST http://localhost:8000/api/v1/sdk-keys/{id}/revoke/ \
  -H "Authorization: Bearer <token>"
```

Both are audited — as `rotate` and `revoke` respectively, because they are different
events to whoever reads the trail after an incident.

## Archiving and Restoring a Flag

```bash
curl -X POST http://localhost:8000/api/v1/projects/{project_key}/flags/{key}/archive/ \
  -H "Authorization: Bearer <token>"

curl -X POST http://localhost:8000/api/v1/projects/{project_key}/flags/{key}/unarchive/ \
  -H "Authorization: Bearer <token>"
```
