import hashlib
from dataclasses import dataclass
from typing import Any, Optional

from django.conf import settings as django_settings
from django.core.cache import cache

from apps.evaluation.queries import EvaluationQuery
from apps.targeting.services import RuleEvaluator

CACHE_TTL = getattr(django_settings, "FLAG_CACHE_TTL", 300)


@dataclass(frozen=True)
class EvaluationResult:
    flag_id: int
    flag_key: str
    result: Any
    result_type: str


class FlagEvaluationService:
    _rule_evaluator = RuleEvaluator()

    def evaluate(
        self, flag_key: str, owner_id: int, user_context: dict, env_id: int
    ) -> EvaluationResult:
        flag_data = self._get_flag_data(flag_key, owner_id, env_id)

        if not flag_data["is_enabled"]:
            return self._from_variation(flag_key, flag_data, flag_data["off_variation"])

        for rule in flag_data["rules"]:
            if self._rule_evaluator.matches(rule, user_context):
                serve = rule.get("serve_variation")
                if serve:
                    return self._from_variation(flag_key, flag_data, serve, default=True)
                return self._from_variation(
                    flag_key, flag_data, flag_data["fallthrough_variation"], default=True
                )

        in_rollout = self._apply_rollout(
            flag_key,
            str(user_context.get("user_id", "")),
            flag_data["rollout_percentage"],
        )
        if in_rollout:
            return self._from_variation(
                flag_key, flag_data, flag_data["fallthrough_variation"], default=True
            )
        return self._from_variation(flag_key, flag_data, flag_data["off_variation"], default=False)

    def _get_flag_data(self, flag_key: str, owner_id: int, env_id: int) -> dict:
        cache_key = f"flags:{owner_id}:{env_id}:{flag_key}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        env_flag = EvaluationQuery.get_active_env_flag(flag_key, owner_id, env_id)
        flag = env_flag.feature_flag

        def _variation_dict(v):
            return {"value": v.value, "value_type": v.value_type} if v else None

        rules = []
        for rule in flag.rules.order_by("priority"):
            rules.append({
                "attribute": rule.attribute,
                "operator": rule.operator,
                "value": rule.value,
                "priority": rule.priority,
                "serve_variation": _variation_dict(rule.serve_variation),
            })

        flag_data = {
            "id": flag.id,
            "flag_type": flag.flag_type,
            "is_enabled": env_flag.is_enabled,
            "rollout_percentage": env_flag.rollout_percentage,
            "rules": rules,
            "off_variation": _variation_dict(flag.off_variation),
            "fallthrough_variation": _variation_dict(flag.fallthrough_variation),
        }
        cache.set(cache_key, flag_data, CACHE_TTL)
        return flag_data

    @staticmethod
    def _from_variation(
        flag_key: str, flag_data: dict, variation: Optional[dict], default: bool = False
    ) -> EvaluationResult:
        if variation is None:
            # Fallback for flags without variations configured (legacy boolean flags).
            # `default` carries the semantic: True for "in rollout bucket", False for "off".
            return EvaluationResult(
                flag_id=flag_data["id"],
                flag_key=flag_key,
                result=default,
                result_type="boolean",
            )
        return EvaluationResult(
            flag_id=flag_data["id"],
            flag_key=flag_key,
            result=variation["value"],
            result_type=variation["value_type"],
        )

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
