import hashlib
from dataclasses import dataclass

from django.conf import settings as django_settings
from django.core.cache import cache
from apps.core.exceptions import FlagNotFoundError
from apps.flags.models import FeatureFlag
from apps.environment.models import EnvironmentFlag
from apps.targeting.services import RuleEvaluator

CACHE_TTL = getattr(django_settings, "FLAG_CACHE_TTL", 300)  # 5 min default


@dataclass(frozen=True)
class EvaluationResult:
    flag_id: int
    flag_key: str
    result: bool


class FlagEvaluationService:
    _rule_evaluator = RuleEvaluator()

    def evaluate(self, flag_key: str, owner_id: int, user_context: dict, env_id: int) -> EvaluationResult:
        """
        Evaluate a flag for a specific environment.
        """
        flag_data = self._get_flag_data(flag_key, owner_id, env_id)

        if not flag_data["is_enabled"]:
            result = False
        elif any(
            self._rule_evaluator.matches(rule, user_context)
            for rule in flag_data["rules"]
        ):
            result = True
        else:
            result = self._apply_rollout(
                flag_key,
                str(user_context.get("user_id", "")),
                flag_data["rollout_percentage"],
            )

        return EvaluationResult(
            flag_id=flag_data["id"],
            flag_key=flag_key,
            result=result,
        )

    def _get_flag_data(self, flag_key: str, owner_id: int, env_id: int) -> dict:
        cache_key = f"flags:{owner_id}:{env_id}:{flag_key}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            env_flag = (
                EnvironmentFlag.objects
                .select_related("feature_flag")
                .prefetch_related("feature_flag__rules")
                .get(
                    feature_flag__key=flag_key,
                    feature_flag__owner_id=owner_id,
                    feature_flag__is_archived=False,
                    environment_id=env_id,
                )
            )
        except EnvironmentFlag.DoesNotExist:
            raise FlagNotFoundError(f"Flag '{flag_key}' not found in environment '{env_id}'")

        flag_data = {
            "id": env_flag.feature_flag.id,
            "is_enabled": env_flag.is_enabled,
            "rollout_percentage": env_flag.rollout_percentage,
            "rules": list(
                env_flag.feature_flag.rules.order_by("priority").values(
                    "attribute", "operator", "value", "priority"
                )
            ),
        }
        cache.set(cache_key, flag_data, CACHE_TTL)
        return flag_data

    @staticmethod
    def invalidate_cache(owner_id: int, flag_key: str, env_id: int) -> None:
        cache.delete(f"flags:{owner_id}:{env_id}:{flag_key}")

    @staticmethod
    def _apply_rollout(flag_key: str, user_id: str, rollout_percentage: int) -> bool:
        if rollout_percentage <= 0:
            return False
        if rollout_percentage >= 100:
            return True
        hash_int = int(
            hashlib.sha256(f"{flag_key}{user_id}".encode()).hexdigest(), 16
        )
        return (hash_int % 100) < rollout_percentage