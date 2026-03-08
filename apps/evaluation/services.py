import hashlib
from dataclasses import dataclass

from django.conf import settings as django_settings
from django.core.cache import cache

from apps.core.exceptions import FlagNotFoundError
from apps.flags.models import FeatureFlag
from apps.targeting.services import RuleEvaluator

CACHE_TTL = getattr(django_settings, "FLAG_CACHE_TTL", 300)  # 5 min default


@dataclass(frozen=True)
class EvaluationResult:
    """
    Value object returned by FlagEvaluationService.evaluate().

    Carrying flag_id alongside the boolean result means callers never need a
    second cache/DB lookup just to obtain the flag's primary key for logging.
    """
    flag_id: int
    flag_key: str
    result: bool


class FlagEvaluationService:
    """
    Core evaluation engine.

    Evaluation algorithm:
      1. Load flag config from cache (key: flags:{owner_id}:{flag_key}).
         On cache miss, query DB and populate cache.
      2. If flag.is_enabled is False -> return False.
      3. Evaluate targeting rules in priority order.
         If any rule matches user_context -> return True.
      4. Apply rollout: hash(flag_key + user_id) % 100 < rollout_percentage.
      5. Return False.

    Cache policy:
      - Stores flag config + rule definitions only.
      - Never caches personal user context data.
      - Invalidated by FlagService on every flag update / delete.
      - Invalidated by RuleViewSet on every rule mutation.
    """

    _rule_evaluator = RuleEvaluator()

    def evaluate(self, flag_key: str, owner_id: int, user_context: dict) -> EvaluationResult:
        """
        Evaluate a flag and return a rich EvaluationResult.

        A single _get_flag_data call provides both the evaluation config and
        the flag's primary key — no second lookup required.
        """
        flag_data = self._get_flag_data(flag_key, owner_id)

        if not flag_data["is_enabled"]:
            result = False
        elif any(
            self._rule_evaluator.matches(rule, user_context)
            for rule in flag_data["rules"]  # already sorted by priority
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

    # ------------------------------------------------------------------
    # Cache layer
    # ------------------------------------------------------------------

    def _get_flag_data(self, flag_key: str, owner_id: int) -> dict:
        cache_key = f"flags:{owner_id}:{flag_key}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            flag = (
                FeatureFlag.objects
                .prefetch_related("rules")
                .get(key=flag_key, owner_id=owner_id)
            )
        except FeatureFlag.DoesNotExist:
            raise FlagNotFoundError(f"Flag '{flag_key}' not found")

        # Serialise only config data — never include personal context
        flag_data = {
            "id": flag.id,
            "is_enabled": flag.is_enabled,
            "rollout_percentage": flag.rollout_percentage,
            "rules": list(
                flag.rules.order_by("priority").values(
                    "attribute", "operator", "value", "priority"
                )
            ),
        }
        cache.set(cache_key, flag_data, CACHE_TTL)
        return flag_data

    @staticmethod
    def invalidate_cache(owner_id: int, flag_key: str) -> None:
        cache.delete(f"flags:{owner_id}:{flag_key}")

    # ------------------------------------------------------------------
    # Rollout
    # ------------------------------------------------------------------

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
