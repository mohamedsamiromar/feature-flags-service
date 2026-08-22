from apps.rules.models import Operator
from apps.core.errors import APIError, Error


class RuleEvaluator:
    """
    Evaluates a single targeting rule against a user context dict.

    Accepts both Rule model instances and plain dicts (as returned from
    queryset.values() and stored in the Redis cache).

    user_context example:
        {"user_id": "u123", "country": "EG", "plan": "pro"}

    Segment operators (`in_segment` / `not_in_segment`) need the flag's
    resolved segment payload, passed as `segments`; every other operator
    ignores it.
    """

    # SegmentEvaluator is resolved lazily: apps.segments.evaluator imports this
    # module for its own rule matching, so importing it at module level would
    # be circular.
    _segment_evaluator_cache = None

    @property
    def _segment_evaluator(self):
        if RuleEvaluator._segment_evaluator_cache is None:
            from apps.segments.evaluator import SegmentEvaluator
            RuleEvaluator._segment_evaluator_cache = SegmentEvaluator()
        return RuleEvaluator._segment_evaluator_cache

    def matches(self, rule, user_context: dict, segments: dict = None) -> bool:
        # Support Rule model instances and cached dicts interchangeably
        if isinstance(rule, dict):
            attribute, operator, value = rule["attribute"], rule["operator"], rule["value"]
        else:
            attribute, operator, value = rule.attribute, rule.operator, rule.value

        if operator in Operator.segment_operators():
            return self._evaluate_segment(operator, value, user_context, segments or {})

        user_value = user_context.get(attribute)
        if user_value is None:
            return False
        return self._evaluate(str(user_value), operator, value)

    def _evaluate_segment(
        self, operator: str, segment_key: str, user_context: dict, segments: dict
    ) -> bool:
        """Resolve a segment-membership rule.

        `segments` is the flag's referenced segments, pre-resolved into the
        cached payload — the hot path never queries.

        An unknown key means the segment was deleted (or never existed) after
        the rule was written. Such a rule does not match, whatever its
        operator: inverting an unresolvable reference would make
        `not_in_segment` match *every* user and turn a dangling reference into
        a full rollout. Failing closed is the only safe reading, so this
        returns False rather than `not inside`.
        """
        segment = segments.get(segment_key)
        if segment is None:
            return False

        inside = self._segment_evaluator.contains(segment, user_context)
        if operator == Operator.NOT_IN_SEGMENT:
            return not inside
        return inside

    def _evaluate(self, user_value: str, operator: str, rule_value: str) -> bool:
        if operator == Operator.EQUALS:
            return user_value == rule_value
        elif operator == Operator.NOT_EQUALS:
            return user_value != rule_value
        elif operator == Operator.CONTAINS:
            return rule_value in user_value
        elif operator == Operator.IN:
            return user_value in [v.strip() for v in rule_value.split(",")]
        elif operator == Operator.NOT_IN:
            return user_value not in [v.strip() for v in rule_value.split(",")]
        elif operator == Operator.GT:
            return float(user_value) > float(rule_value)
        elif operator == Operator.LT:
            return float(user_value) < float(rule_value)
        raise APIError(Error.INVALID_OPERATOR, extra=[operator])
