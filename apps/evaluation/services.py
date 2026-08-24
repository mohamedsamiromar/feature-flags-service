import hashlib
from dataclasses import dataclass
from typing import Any, Optional

from django.conf import settings as django_settings
from django.core.cache import cache

from apps.core.errors import APIError
from apps.evaluation.queries import EvaluationQuery
from apps.rules.models import Operator
from apps.segments.queries import SegmentQuery
from apps.targeting.services import RuleEvaluator

CACHE_TTL = getattr(django_settings, "FLAG_CACHE_TTL", 300)

# Runtime backstop for prerequisite recursion. Cycles are rejected when a
# prerequisite is created, so reaching this means the graph was corrupted
# some other way (a direct DB write, a migration). Evaluation fails closed
# rather than recursing forever and taking the SDK endpoint down with it.
MAX_PREREQUISITE_DEPTH = 10


@dataclass(frozen=True)
class EvaluationResult:
    flag_id: int
    flag_key: str
    result: Any
    result_type: str
    # Which variation produced `result`. Prerequisites compare identity, not
    # value, because two variations of a flag may carry the same value.
    # None when the flag has no variations configured (legacy boolean flags).
    variation_id: Optional[int] = None


class FlagEvaluationService:
    _rule_evaluator = RuleEvaluator()

    def evaluate(
        self,
        flag_key: str,
        project_id: int,
        user_context: dict,
        env_id: int,
        _chain: tuple = (),
    ) -> EvaluationResult:
        """Resolve `flag_key` for a user.

        `_chain` carries the prerequisite flags already being resolved further
        up the stack; it is internal and callers never pass it.
        """
        flag_data = self._get_flag_data(flag_key, project_id, env_id)

        if not flag_data["is_enabled"]:
            return self._from_variation(flag_key, flag_data, flag_data["off_variation"])

        # Prerequisites gate everything below, individual targets included: a
        # user explicitly targeted on this flag still does not get it while a
        # prerequisite is unmet.
        if not self._prerequisites_met(flag_data, project_id, user_context, env_id, _chain, flag_key):
            return self._from_variation(flag_key, flag_data, flag_data["off_variation"])

        targeted = flag_data.get("targets", {}).get(str(user_context.get("user_id", "")))
        if targeted is not None:
            return self._from_variation(flag_key, flag_data, targeted)

        segments = flag_data.get("segments", {})
        for rule in flag_data["rules"]:
            if self._rule_evaluator.matches(rule, user_context, segments):
                # A matching rule wins outright — evaluation never falls through
                # to a later rule. Its own rollout decides whether this user is
                # in the slice being served.
                if not self._in_rule_rollout(rule, flag_key, user_context):
                    return self._from_variation(
                        flag_key, flag_data, flag_data["off_variation"], default=False
                    )
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

    def _get_flag_data(self, flag_key: str, project_id: int, env_id: int) -> dict:
        cache_key = f"flags:{project_id}:{env_id}:{flag_key}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        env_flag = EvaluationQuery.get_active_env_flag(flag_key, project_id, env_id)
        flag = env_flag.feature_flag

        def _variation_dict(v):
            if not v:
                return None
            return {"id": v.id, "value": v.value, "value_type": v.value_type}

        rules = []
        for rule in flag.rules.order_by("priority"):
            rules.append({
                # `id` salts the per-rule bucketing so two rules at the same
                # percentage do not select the same slice of users.
                "id": rule.id,
                "attribute": rule.attribute,
                "operator": rule.operator,
                "value": rule.value,
                "priority": rule.priority,
                "rollout_percentage": rule.rollout_percentage,
                "serve_variation": _variation_dict(rule.serve_variation),
            })

        # user_key → variation, so the hot path is a dict lookup, not a scan.
        targets = {
            target.user_key: _variation_dict(target.variation)
            for target in flag.targets.all()
        }

        # Only the segments this flag actually references are resolved, and only
        # when the cache entry is built — the SDK hot path never queries them.
        #
        # NOTE: the payload contains Python `set` objects (see
        # SegmentQuery.evaluation_payload). That is fine for the Redis cache,
        # which pickles, but `flag_data` must NEVER be handed to a Celery task:
        # CELERY_TASK_SERIALIZER is "json" and json.dumps raises on a set. Pass
        # the evaluated *result* to tasks, never the flag config that produced
        # it. TestEvaluationTaskArgsStayJsonSafe pins this.
        segment_keys = [
            rule["value"] for rule in rules
            if rule["operator"] in Operator.segment_operators()
        ]
        segments = SegmentQuery.evaluation_payload(project_id, segment_keys)

        prerequisites = [
            {
                "flag_key": p.prerequisite_flag.key,
                "required_variation_id": p.required_variation_id,
            }
            for p in flag.prerequisites.all()
        ]

        flag_data = {
            "id": flag.id,
            "flag_type": flag.flag_type,
            "is_enabled": env_flag.is_enabled,
            "rollout_percentage": env_flag.rollout_percentage,
            "rules": rules,
            "targets": targets,
            "segments": segments,
            "prerequisites": prerequisites,
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
            # .get: cache entries written before variation ids were carried
            # outlive a deploy by up to the TTL.
            variation_id=variation.get("id"),
        )

    @staticmethod
    def invalidate_cache(project_id: int, flag_key: str, env_id: int) -> None:
        cache.delete(f"flags:{project_id}:{env_id}:{flag_key}")

    def _prerequisites_met(
        self,
        flag_data: dict,
        project_id: int,
        user_context: dict,
        env_id: int,
        chain: tuple,
        flag_key: str,
    ) -> bool:
        """Whether every prerequisite of this flag is serving its required
        variation for this user.

        Fails closed in every uncertain case — a cycle, an unreachable or
        archived prerequisite, or a prerequisite with no variation resolved.
        A dependent flag that cannot confirm its gate must stay off rather than
        guess its way open.
        """
        prerequisites = flag_data.get("prerequisites", [])
        if not prerequisites:
            return True

        if flag_key in chain or len(chain) >= MAX_PREREQUISITE_DEPTH:
            return False

        next_chain = chain + (flag_key,)
        for prerequisite in prerequisites:
            try:
                resolved = self.evaluate(
                    flag_key=prerequisite["flag_key"],
                    project_id=project_id,
                    user_context=user_context,
                    env_id=env_id,
                    _chain=next_chain,
                )
            except APIError:
                # Prerequisite archived, deleted, or not configured in this
                # environment. Deleting/archiving a flag that gates another is
                # refused, so this is the defensive path, not the normal one.
                return False

            if resolved.variation_id is None:
                return False
            if resolved.variation_id != prerequisite["required_variation_id"]:
                return False
        return True

    @classmethod
    def _in_rule_rollout(cls, rule: dict, flag_key: str, user_context: dict) -> bool:
        """Whether this user falls inside a matched rule's own rollout slice.

        Defaults to 100 for entries written before rules had a rollout — a
        cached payload outlives a deploy by up to the TTL, and a missing key
        must mean "applies to everyone", never "applies to no one".

        Salted with the rule id so two rules at the same percentage target
        different slices instead of the same users.
        """
        percentage = rule.get("rollout_percentage", 100)
        if percentage >= 100:
            return True
        return cls._apply_rollout(
            flag_key,
            str(user_context.get("user_id", "")),
            percentage,
            salt=f"rule:{rule.get('id', '')}:",
        )

    @staticmethod
    def _apply_rollout(
        flag_key: str, user_id: str, rollout_percentage: int, salt: str = ""
    ) -> bool:
        """Deterministically bucket a user into `rollout_percentage`.

        `salt` MUST default to "" and stay empty for the flag-level rollout:
        the hash decides which users already have a flag, so changing its
        inputs would re-bucket everyone and flip live flags on deploy.
        """
        if rollout_percentage <= 0:
            return False
        if rollout_percentage >= 100:
            return True
        hash_int = int(
            hashlib.sha256(f"{salt}{flag_key}{user_id}".encode()).hexdigest(), 16
        )
        return (hash_int % 100) < rollout_percentage
