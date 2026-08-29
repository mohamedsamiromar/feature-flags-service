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
    def get_active_env_flag(flag_key: str, project_id: int, env_id: int) -> EnvironmentFlag:
        try:
            return (
                EnvironmentFlag.objects
                .select_related(
                    "feature_flag__off_variation",
                    "feature_flag__fallthrough_variation",
                )
                .prefetch_related(
                    "feature_flag__rules__serve_variation",
                    "feature_flag__targets__variation",
                    "feature_flag__prerequisites__prerequisite_flag",
                )
                .get(
                    feature_flag__key=flag_key,
                    feature_flag__project_id=project_id,
                    feature_flag__is_archived=False,
                    environment_id=env_id,
                )
            )
        except EnvironmentFlag.DoesNotExist:
            raise APIError(Error.INSTANCE_NOT_FOUND, extra=["Flag"])

    @staticmethod
    def active_flag_keys(project_id: int, env_id: int) -> list:
        """Every non-archived flag key configured in this environment.

        Deliberately a `values_list` and nothing more: the bulk evaluate path
        runs this on every request (a warm cache cannot tell you which flags
        *exist*), so it must stay one cheap indexed read. The expensive
        per-flag payload is what the cache holds.
        """
        return list(
            EnvironmentFlag.objects
            .filter(
                environment_id=env_id,
                feature_flag__project_id=project_id,
                feature_flag__is_archived=False,
            )
            .values_list("feature_flag__key", flat=True)
        )

    @staticmethod
    def get_active_env_flags(flag_keys, project_id: int, env_id: int) -> list:
        """Bulk sibling of `get_active_env_flag` — one query for many flags.

        Used to warm the cache misses of a bulk evaluation. Missing keys are
        simply absent from the result (no 404): the caller decides what an
        unresolvable flag means, and for bulk evaluation that is "omit it".

        The `select_related`/`prefetch_related` here MUST stay identical to
        `get_active_env_flag`'s — both feed the same payload builder, and a
        divergence shows up as N+1 queries on the bulk warm path only.
        """
        if not flag_keys:
            return []
        return list(
            EnvironmentFlag.objects
            .select_related(
                "feature_flag__off_variation",
                "feature_flag__fallthrough_variation",
            )
            .prefetch_related(
                "feature_flag__rules__serve_variation",
                "feature_flag__targets__variation",
                "feature_flag__prerequisites__prerequisite_flag",
            )
            .filter(
                feature_flag__key__in=list(flag_keys),
                feature_flag__project_id=project_id,
                feature_flag__is_archived=False,
                environment_id=env_id,
            )
        )
