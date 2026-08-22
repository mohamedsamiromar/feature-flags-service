"""Segment membership resolution — pure logic, no ORM, no cache.

Lives beside the segment models rather than in ``targeting`` because the
precedence rules below *are* the definition of a segment; ``RuleEvaluator``
just asks the question.
"""

from apps.rules.models import Operator
from apps.targeting.services import RuleEvaluator


class SegmentEvaluator:
    """Decides whether a user context falls inside a segment.

    Operates on the cached dict shape built by
    ``SegmentQuery.evaluation_payload``::

        {"included": {"alice"}, "excluded": {"bob"}, "rules": [ {...}, ... ]}

    Precedence, highest first:

    1. **Excluded** — an explicit exclusion always wins, even over a rule the
       user matches. This is what makes exclusion usable as a safety valve:
       "everyone on the pro plan except this one account".
    2. **Included** — an explicit inclusion admits a user who matches no rule.
    3. **Rules** — ANY rule matching admits the user (OR, not AND), so a
       segment reads as "people who are X, or Y, or Z".
    4. Otherwise the user is outside the segment.

    A segment with no targets and no rules is empty, never universal — an
    unconfigured segment must not silently match everyone.
    """

    _rule_evaluator = RuleEvaluator()

    def contains(self, segment: dict, user_context: dict) -> bool:
        user_key = str(user_context.get("user_id", ""))

        if user_key in segment["excluded"]:
            return False
        if user_key in segment["included"]:
            return True
        return any(
            self._rule_evaluator.matches(rule, user_context)
            for rule in segment["rules"]
            # Segments do not nest. A segment operator here would be resolved
            # against an empty segment map, and `not_in_segment` would then
            # match every user — making the whole segment universal. The model
            # forbids these operators; this is the belt-and-braces guard for
            # any row that predates or bypasses that.
            if rule["operator"] not in Operator.segment_operators()
        )
