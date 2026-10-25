---
paths:
  - "apps/**/migrations/**"
---

# Migrations

```bash
docker compose run --rm web python manage.py makemigrations <app_name>
docker compose run --rm web python manage.py migrate
docker compose run --rm web python manage.py makemigrations --check --dry-run   # CI enforces this
```

- **Never edit a migration that is already committed on `main`.** Write a new one.
- A new numeric constraint is enforced at three layers: the serializer, the model validator
  and a PostgreSQL `CheckConstraint` in the migration
  ([ADR 0017](../../docs/adr/0017-three-layer-numeric-validation.md)).

## Data migrations

- `organizations/0002_backfill_personal_orgs` gave existing users the same tenancy shape
  that `RegistrationService.register` now gives new ones. If either changes, the other
  is the reference (see [tenancy.md](tenancy.md)).
- `flags/0014_featureflag_client_side_available` adds the column with `default=True` and
  then alters it to `default=False`. Existing flags stay visible to live browser SDKs,
  while new flags start hidden.
- Data migrations have tests. `apps/flags/tests/test_migration_client_side_available.py`
  replays the migration for real with `MigrationExecutor`, under
  `@pytest.mark.django_db(transaction=True)`, rather than trusting its operations to mean
  what they say. `apps/organizations/tests/test_migration_backfill.py` imports the
  migration module and tests its helper functions.
