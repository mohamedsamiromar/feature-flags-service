from apps.rules.models import Operator
from apps.core.exceptions import InvalidOperatorError


class RuleEvaluator:
    """
    Evaluates a single targeting rule against a user context dict.

    Accepts both Rule model instances and plain dicts (as returned from
    queryset.values() and stored in the Redis cache).

    user_context example:
        {"user_id": "u123", "country": "EG", "plan": "pro"}
    """

    def matches(self, rule, user_context: dict) -> bool:
        # Support Rule model instances and cached dicts interchangeably
        if isinstance(rule, dict):
            attribute, operator, value = rule["attribute"], rule["operator"], rule["value"]
        else:
            attribute, operator, value = rule.attribute, rule.operator, rule.value

        user_value = user_context.get(attribute)
        if user_value is None:
            return False
        return self._evaluate(str(user_value), operator, value)

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
        raise InvalidOperatorError(f"Unknown operator: '{operator}'")
