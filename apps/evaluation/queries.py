"""Query layer for the evaluation hot path.

Only the raw DB fetch lives here; the evaluation service keeps the caching,
dict-shaping, and rollout hashing. The `select_related`/`prefetch_related` here
must stay in lockstep with what the service reads to avoid N+1 regressions on
the SDK evaluate endpoint.
"""

from apps.core.errors import APIError, Error
from apps.environment.models import EnvironmentFlag


class EvaluationQuery:
    @staticmethod
    def get_active_env_flag(flag_key: str, owner_id: int, env_id: int) -> EnvironmentFlag:
        try:
            return (
                EnvironmentFlag.objects
                .select_related(
                    "feature_flag__off_variation",
                    "feature_flag__fallthrough_variation",
                )
                .prefetch_related("feature_flag__rules__serve_variation")
                .get(
                    feature_flag__key=flag_key,
                    feature_flag__owner_id=owner_id,
                    feature_flag__is_archived=False,
                    environment_id=env_id,
                )
            )
        except EnvironmentFlag.DoesNotExist:
            raise APIError(Error.INSTANCE_NOT_FOUND, extra=["Flag"])
