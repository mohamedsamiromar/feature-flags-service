# SKILL.md — Feature Flag Engine

Step-by-step workflows for common development tasks on this project.

---

## Adding a New Flag Feature (e.g. a new action endpoint)

1. Add the action to `FlagService` in [apps/flags/services.py](apps/flags/services.py).
2. Register the `@action` on `FlagViewSet` in [apps/flags/views.py](apps/flags/views.py).
3. If the action mutates the flag, call `AuditService.log(...)` and `FlagEvaluationService.invalidate_cache(...)` inside the service method.
4. Write tests under [apps/flags/tests/](apps/flags/tests/).

## Adding a New Targeting Operator

1. Add the operator constant to `Rule.Operator` in [apps/rules/models.py](apps/rules/models.py).
2. Implement the match branch in `RuleEvaluator.matches()` in [apps/targeting/services.py](apps/targeting/services.py).
3. Add a test case in [apps/rules/tests.py](apps/rules/tests.py) covering both a match and a non-match.

## Adding a New Environment

Via the API (no code change required):

```bash
curl -X POST http://localhost:8000/api/v1/environments/ \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"name": "staging"}'
```

Then link a flag to it:

```bash
curl -X PATCH http://localhost:8000/api/v1/environments/{env_id}/flags/{flag_id}/ \
  -H "Authorization: Bearer <token>" \
  -d '{"is_enabled": true, "rollout_percentage": 50}'
```

## Issuing and Using an SDK Key

```bash
# Create a server key for an environment
curl -X POST http://localhost:8000/api/v1/sdk-keys/ \
  -H "Authorization: Bearer <token>" \
  -d '{"name": "Prod Server", "key_type": "server", "environment": 1}'
# Response includes the full key exactly once — save it immediately.

# Evaluate a flag
curl -X POST http://localhost:8000/api/v1/sdk/evaluate/ \
  -H "X-SDK-Key: sdk_srv_<token>" \
  -d '{"flag_key": "dark-mode", "user_context": {"user_id": "u_1", "plan": "pro"}}'
```

## Running a Subset of Tests

```bash
# Flags app only
docker compose run --rm web pytest apps/flags/ -v

# SDK key tests only
docker compose run --rm web pytest apps/sdk_keys/tests/ -v

# SDK evaluate tests only
docker compose run --rm web pytest apps/sdk/tests/ -v

# Run a single test by name
docker compose run --rm web pytest apps/flags/ -k "test_archive" -v
```

## Creating a Migration

```bash
docker compose exec web python manage.py makemigrations <app_name>
docker compose exec web python manage.py migrate
```

## Debugging Evaluation (Cache Inspection)

```bash
# Connect to the Redis container
docker compose exec redis redis-cli -n 1

# List all flag cache keys
KEYS flags:*

# Inspect a specific key
GET flags:<owner_id>:<env_id>:<flag_key>

# Manually invalidate a cached flag
DEL flags:<owner_id>:<env_id>:<flag_key>
```

## Checking Celery Task Output

```bash
# Tail the Celery worker logs
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
# Rotate (revoke old, issue new — returns new full key once)
curl -X POST http://localhost:8000/api/v1/sdk-keys/{id}/rotate/ \
  -H "Authorization: Bearer <token>"

# Revoke only
curl -X POST http://localhost:8000/api/v1/sdk-keys/{id}/revoke/ \
  -H "Authorization: Bearer <token>"
```

## Archiving and Restoring a Flag

```bash
curl -X POST http://localhost:8000/api/v1/flags/{id}/archive/ \
  -H "Authorization: Bearer <token>"

curl -X POST http://localhost:8000/api/v1/flags/{id}/unarchive/ \
  -H "Authorization: Bearer <token>"
```
