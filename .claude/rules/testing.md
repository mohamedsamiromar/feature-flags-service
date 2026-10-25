---
paths:
  - "apps/**/tests/**"
  - "conftest.py"
---

# Testing

Run tests with `docker compose run --rm web pytest apps/<app>/ -q`. The full suite takes
about a minute.

## Fixtures and factories

- All factories live in the root `conftest.py`: `UserFactory`, `OrganizationFactory`,
  `MembershipFactory`, `ProjectFactory`, `FeatureFlagFactory`, `EnvironmentFactory`,
  `EnvironmentFlagFactory`, `VariationFactory`, `SDKKeyFactory`.
- `FeatureFlagFactory` and `EnvironmentFactory` accept an `owner=<user>` shim, which
  auto-provisions a deterministic personal project. `personal_project_for(user)` returns
  it, so a flag and an environment created with the same `owner=` land in the same project.
- Use `auth_client` for JWT calls and `api_client` for SDK-key calls. The SDK endpoints
  **reject JWTs**. `sdk_key` is an active server key.
- `SDKKeyFactory._create` attaches `instance._full_key` for tests that authenticate with
  the key. This is test-only; real `SDKKey` instances have no such attribute.
- `base` gives the project-nested flag URL prefix, and `env_base` the environment one.

```python
def test_flag_create(auth_client, base):
    response = auth_client.post(f"{base}/", {"name": "My Flag", "key": "my-flag"}, format="json")
    assert response.status_code == 201
```

## Real infrastructure

- Tests hit real PostgreSQL and Redis. **Do not mock the ORM.**
- An autouse fixture clears the Redis cache around every test. Do not remove it. Redis
  outlives the test database, so a recycled primary key could otherwise read a stale entry
  from an earlier run.
- Tests that assert on rows written by a Celery task need the `eager_celery` fixture
  (currently defined in `apps/sdk/tests/test_impressions.py`). Without it, `.delay()` hands
  the batch to the real broker, and the worker writes it to the *development* database, so
  the assertion silently checks nothing.

## Query counts

Round-trip budgets are pinned with `django_assert_num_queries`, in
`apps/sdk/tests/test_bulk_evaluate.py`, `test_config_download.py`,
`test_client_side_availability.py`, `test_impressions.py` and
`apps/segments/tests/test_segment_evaluation.py`. If a change moves one of those numbers,
that is a finding to explain, not a constant to bump.

## Test through the layer you claim works

Service-level tests do not prove an endpoint works. A required serializer field once made
an entire feature unreachable over HTTP while every service test passed. Any new API
surface needs at least one test through the serializer.

## Verify a test can fail

After writing a test for a safety property, break the property and confirm that *that*
test fails. A test asserting that two rollout slices differ once passed with the salt
removed entirely: the two rules were on different flags, and the flag key separated them
anyway.
