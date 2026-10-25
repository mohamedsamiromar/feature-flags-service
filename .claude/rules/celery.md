---
paths:
  - "apps/**/tasks.py"
  - "apps/sdk/views.py"
  - "apps/evaluation/services.py"
  - "config/celery.py"
---

# Celery

## The JSON boundary

`CELERY_TASK_SERIALIZER = "json"`, and the cached flag config contains Python `set`s
(segment membership).

- **Never pass `flag_data` or a segment payload to a Celery task.** Pass evaluated results
  and scalars.
- `log_evaluations`, the ingest primitive behind `POST /sdk/impressions/`, takes
  `evaluations=[{flag_id, result, context_data}, ...]`, with `context_data` per record.
  Keep that list to scalars and JSON values.
- The client bootstrap endpoint dispatches **no** task at all (see [sdk.md](sdk.md)).
- Evaluation logging is always async. No HTTP response waits on the write.

## Changing a task: restart the worker

Celery does not auto-reload, and the volume mount does not help. After editing `tasks.py`:

```bash
docker compose restart celery
```

Otherwise the web container queues the new task shape, and the old worker rejects it
(`Reject: missing 1 required keyword-only argument`), silently dropping every message in
between. This bit during the impression-batching work: the endpoint returned `202`, and
nothing was written.

In a real deploy, ordering matters. Deploy workers before web, or make the new argument
optional for one release.

Tests that assert on rows written by a task need `eager_celery`: see [testing.md](testing.md).
