---
paths:
  - "apps/*/services.py"
  - "apps/audit/**"
  - "apps/*/models.py"
---

# Audit logging

Mutations call `AuditService.log(...)`, using `AuditService.snapshot(entity)` to capture
`old_value` first.

## Deletes use `log_delete`, never `log`

Django clears `instance.pk` on `.delete()`, so a plain `log` records `entity_id="None"` and
detaches the entry from the row it describes. The helper restores the pk from the snapshot:

```python
old_snapshot = AuditService.snapshot(obj)
Query.delete(obj)
AuditService.log_delete(user=user, entity=obj, old_value=old_snapshot)
```

## Only successful mutations are audited

A write rejected with a 400 or 409 changed nothing, and an entry for it makes the trail lie.

## `REDACTED_FIELDS` strips secrets from snapshots

`AuditService.REDACTED_FIELDS` is keyed by `Model._meta.model_name`. Today it holds
`SDKKey.hashed_key` and `Environment.secure_mode_secret`. It is a registry rather than a
per-call argument, because an argument is something a future caller forgets, and
forgetting it writes the secret into a second table with different access rules. Add to it
whenever a model gains a credential-shaped field.

## Coverage is complete. Keep it that way.

Flags, variations, environments, segments, targets, prerequisites, rules, SDK keys,
organizations, memberships, invitations and projects are all audited. Signup provisions
its org, project and environments through the query layer and audits them explicitly. An
audit invariant with one exception is the one found during an incident.
