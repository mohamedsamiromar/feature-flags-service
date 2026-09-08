from __future__ import annotations

from typing import Any

from celery import shared_task


@shared_task(
    name="evaluation.log_evaluation",
    # Retry up to 3 times with exponential back-off if the DB is temporarily
    # unavailable. After all retries are exhausted the failure is logged by
    # Celery but the HTTP response has already been returned to the caller.
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
    # Evaluations are high-volume; keep the result in the backend only when
    # explicitly requested so we don't flood Redis with task results.
    ignore_result=True,
)
def log_evaluation(*, flag_id: int, user_id: int | None, result: Any, context_data: dict) -> None:
    """
    Persist a single flag evaluation record asynchronously.

    Args:
        flag_id:      Primary key of the evaluated FeatureFlag.
        user_id:      Primary key of the requesting user (None for anonymous).
        result:       The served variation value — boolean, string, number, or
                      JSON object.
        context_data: The user_context dict submitted with the evaluation request.
                      Stored for debugging / analytics; treat as potentially
                      sensitive and avoid logging PII beyond what is necessary.
    """
    # Import inside the task body to avoid import-time side-effects when Celery
    # workers are starting up before Django is fully initialised.
    from apps.evaluation.models import EvaluationLog

    EvaluationLog.objects.create(
        flag_id=flag_id,
        user_id=user_id,
        result=result,
        context_data=context_data,
    )


@shared_task(
    name="evaluation.log_evaluations",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
    ignore_result=True,
)
def log_evaluations(*, evaluations: list, user_id: int | None) -> None:
    """
    Persist a batch of flag evaluations in one insert.

    The ingest primitive for `POST /sdk/impressions/`, where an SDK that
    evaluated locally reports what it actually served. Dispatching
    `log_evaluation` per impression would queue N tasks and run N inserts for
    one HTTP request; this takes the whole batch.

    Each record carries its own `context_data` rather than the batch sharing
    one. A server-side SDK flushing impressions has evaluated many *users*
    since the last flush, so one context per batch would force one request per
    user and give back the round trips this endpoint exists to save. The rows
    are unchanged either way — `EvaluationLog.context_data` was always per
    row — so the cost is message size, not storage.

    NOT wired to `POST /sdk/flags/evaluate/`. That endpoint resolves every flag
    in an environment, but a bootstrap is a download, not a read — logging all
    of them as impressions would inflate `EvaluationLog` with rows nobody
    consumed. See `SDKEvaluateAllFlagsView`.

    Args:
        evaluations:  ``[{"flag_id": int, "result": <JSON value>,
                          "context_data": dict}, ...]``.
                      Scalars, dicts, and JSON values only — NEVER the cached
                      flag config, which contains Python sets that this task's
                      json serializer cannot encode.
        user_id:      Primary key of the requesting user (None for SDK calls,
                      where the key is the principal).
    """
    from apps.evaluation.models import EvaluationLog

    if not evaluations:
        return

    EvaluationLog.objects.bulk_create([
        EvaluationLog(
            flag_id=evaluation["flag_id"],
            user_id=user_id,
            result=evaluation["result"],
            context_data=evaluation.get("context_data", {}),
        )
        for evaluation in evaluations
    ])
