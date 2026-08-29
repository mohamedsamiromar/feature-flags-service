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


def _variation_dict(variation) -> Optional[dict]:
    """The cached shape of a variation. `id` is carried because prerequisites
    compare variation identity, not value — two variations of a flag may hold
    the same value."""
    if not variation:
        return None
    return {"id": variation.id, "value": variation.value, "value_type": variation.value_type}


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
        _preloaded: Optional[dict] = None,
    ) -> EvaluationResult:
        """Resolve `flag_key` for a user.

        `_chain` carries the prerequisite flags already being resolved further
        up the stack; `_preloaded` is the `{flag_key: flag_data}` map a bulk
        evaluation has already resolved for this environment. Both are
        internal and callers never pass them.
        """
        flag_data = self._get_flag_data(flag_key, project_id, env_id, _preloaded)

        if not flag_data["is_enabled"]:
            return self._from_variation(flag_key, flag_data, flag_data["off_variation"])

        # Prerequisites gate everything below, individual targets included: a
        # user explicitly targeted on this flag still does not get it while a
        # prerequisite is unmet.
        if not self._prerequisites_met(
            flag_data, project_id, user_context, env_id, _chain, flag_key, _preloaded
        ):
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

    def evaluate_all(self, project_id: int, env_id: int, user_context: dict) -> list:
        """Resolve every flag configured in this environment for one user.

        The bulk counterpart of `evaluate`, and the reason it exists: an SDK
        bootstrapping a session needs every flag at once, and doing that one
        HTTP call per flag is the thing this endpoint replaces.

        Cost is a fixed number of round trips, not one set per flag:

        - ONE indexed query for the environment's flag keys. Unavoidable even
          on a fully warm cache — the cache knows each flag's config, but not
          which flags exist.
        - ONE `get_many` against Redis for all of their payloads.
        - On misses only: ONE query for those flags, ONE for the union of the
          segments they reference, and ONE `set_many` to warm them.

        The resolved payloads are handed to `evaluate` as `_preloaded`, so
        per-flag evaluation — and prerequisite resolution underneath it —
        touches neither Redis nor the database again.
        """
        flag_keys = EvaluationQuery.active_flag_keys(project_id, env_id)
        if not flag_keys:
            return []

        # A flag archived between the index read and the fetch is absent from
        # `payloads` and so is simply omitted from the response, leaving the
        # SDK on its own fallback default. That is the same fail-closed answer
        # the engine gives everywhere else it cannot confirm something.
        payloads = self._preload_flag_data(flag_keys, project_id, env_id)

        return [
            self.evaluate(
                flag_key=flag_key,
                project_id=project_id,
                user_context=user_context,
                env_id=env_id,
                _preloaded=payloads,
            )
            for flag_key in sorted(payloads)
        ]

    def _preload_flag_data(self, flag_keys, project_id: int, env_id: int) -> dict:
        """`{flag_key: flag_data}` for `flag_keys`, warming any cache misses.

        Keys that cannot be resolved are absent from the result rather than
        raising — see `evaluate_all`.
        """
        by_cache_key = {self._cache_key(project_id, env_id, key): key for key in flag_keys}
        payloads = {
            by_cache_key[cache_key]: data
            for cache_key, data in cache.get_many(list(by_cache_key)).items()
        }

        missing = [key for key in flag_keys if key not in payloads]
        if not missing:
            return payloads

        env_flags = EvaluationQuery.get_active_env_flags(missing, project_id, env_id)

        # Rules are built first so the segments every missing flag references
        # resolve in ONE query, instead of one per flag as the single-flag
        # path necessarily does.
        rules_by_flag = {
            env_flag.feature_flag.key: self._build_rules(env_flag.feature_flag)
            for env_flag in env_flags
        }
        all_segment_keys = set()
        for rules in rules_by_flag.values():
            all_segment_keys.update(self._segment_keys(rules))
        segments = SegmentQuery.evaluation_payload(project_id, all_segment_keys)

        warmed = {}
        for env_flag in env_flags:
            flag_key = env_flag.feature_flag.key
            rules = rules_by_flag[flag_key]
            # Each entry carries only the segments its own rules name, so a
            # bulk warm writes byte-for-byte the payload a single evaluation
            # of that flag would have written.
            own_segments = {
                key: segments[key]
                for key in self._segment_keys(rules)
                if key in segments
            }
            flag_data = self._build_flag_data(env_flag, rules, own_segments)
            warmed[flag_key] = flag_data
            payloads[flag_key] = flag_data

        cache.set_many(
            {
                self._cache_key(project_id, env_id, key): data
                for key, data in warmed.items()
            },
            CACHE_TTL,
        )
        return payloads

    def _get_flag_data(
        self,
        flag_key: str,
        project_id: int,
        env_id: int,
        preloaded: Optional[dict] = None,
    ) -> dict:
        """One flag's evaluation payload: preloaded map → cache → database.

        Consulting `preloaded` first is what keeps a bulk request at a single
        Redis round trip rather than one per flag, prerequisite chains
        included.
        """
        if preloaded is not None:
            hit = preloaded.get(flag_key)
            if hit is not None:
                return hit

        cache_key = self._cache_key(project_id, env_id, flag_key)
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        env_flag = EvaluationQuery.get_active_env_flag(flag_key, project_id, env_id)
        rules = self._build_rules(env_flag.feature_flag)
        segments = SegmentQuery.evaluation_payload(project_id, self._segment_keys(rules))
        flag_data = self._build_flag_data(env_flag, rules, segments)
        cache.set(cache_key, flag_data, CACHE_TTL)
        return flag_data

    @staticmethod
    def _cache_key(project_id: int, env_id: int, flag_key: str) -> str:
        return f"flags:{project_id}:{env_id}:{flag_key}"

    @staticmethod
    def _build_rules(flag) -> list:
        """Rules in priority order, sorted in Python on purpose.

        `flag.rules.order_by(...)` builds a NEW queryset, which throws away the
        `rules__serve_variation` prefetch and costs a query for the rules plus
        one per rule for its variation — a query per flag on the single path,
        and N of them on a bulk warm. `.all()` reads the prefetch cache, and
        `sorted` is stable, so equal priorities keep the order the prefetch
        returned (the DB `order_by` gave no tiebreak either).
        """
        return [
            {
                # `id` salts the per-rule bucketing so two rules at the same
                # percentage do not select the same slice of users.
                "id": rule.id,
                "attribute": rule.attribute,
                "operator": rule.operator,
                "value": rule.value,
                "priority": rule.priority,
                "rollout_percentage": rule.rollout_percentage,
                "serve_variation": _variation_dict(rule.serve_variation),
            }
            for rule in sorted(flag.rules.all(), key=lambda rule: rule.priority)
        ]

    @staticmethod
    def _segment_keys(rules) -> list:
        """The segments a flag's rules reference. For segment operators the
        rule's `value` holds the segment key."""
        return [
            rule["value"] for rule in rules
            if rule["operator"] in Operator.segment_operators()
        ]

    @staticmethod
    def _build_flag_data(env_flag, rules: list, segments: dict) -> dict:
        """Assemble the cached evaluation payload for one flag.

        Shared by the single-flag and bulk paths so the two can never write
        differently shaped entries into the same cache.

        NOTE: `segments` contains Python `set` objects (see
        SegmentQuery.evaluation_payload). That is fine for the Redis cache,
        which pickles, but the returned payload must NEVER be handed to a
        Celery task: CELERY_TASK_SERIALIZER is "json" and json.dumps raises on
        a set. Pass the evaluated *result* to tasks, never the flag config that
        produced it. TestEvaluationTaskArgsStayJsonSafe pins this.
        """
        flag = env_flag.feature_flag
        return {
            "id": flag.id,
            "flag_type": flag.flag_type,
            "is_enabled": env_flag.is_enabled,
            "rollout_percentage": env_flag.rollout_percentage,
            "rules": rules,
            # user_key → variation, so the hot path is a dict lookup, not a scan.
            "targets": {
                target.user_key: _variation_dict(target.variation)
                for target in flag.targets.all()
            },
            "segments": segments,
            "prerequisites": [
                {
                    "flag_key": p.prerequisite_flag.key,
                    "required_variation_id": p.required_variation_id,
                }
                for p in flag.prerequisites.all()
            ],
            "off_variation": _variation_dict(flag.off_variation),
            "fallthrough_variation": _variation_dict(flag.fallthrough_variation),
        }

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

    @classmethod
    def invalidate_cache(cls, project_id: int, flag_key: str, env_id: int) -> None:
        cache.delete(cls._cache_key(project_id, env_id, flag_key))

    def _prerequisites_met(
        self,
        flag_data: dict,
        project_id: int,
        user_context: dict,
        env_id: int,
        chain: tuple,
        flag_key: str,
        preloaded: Optional[dict] = None,
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
                    _preloaded=preloaded,
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
