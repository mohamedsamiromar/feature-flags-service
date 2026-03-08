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
def log_evaluation(*, flag_id: int, user_id: int | None, result: bool, context_data: dict) -> None:
    """
    Persist a single flag evaluation record asynchronously.

    Args:
        flag_id:      Primary key of the evaluated FeatureFlag.
        user_id:      Primary key of the requesting user (None for anonymous).
        result:       Boolean outcome of the evaluation.
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
