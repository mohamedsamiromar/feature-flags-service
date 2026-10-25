# 0011 — Evaluation logging is asynchronous

**Status:** Accepted

## Context

Writing an `EvaluationLog` row synchronously puts a DB write in the hot path of every
flag check.

## Decision

`EvaluationLog` writes go through Celery. `log_evaluations` takes a whole batch and
carries `context_data` **per record**, because an impression flush spans many users and a
shared context would force one request each.

## Consequences

- The HTTP response returns immediately. The worker writes with retries.
- A failed write is invisible to the caller.
- Task arguments cross a JSON boundary. See [celery rules](../../.claude/rules/celery.md).
