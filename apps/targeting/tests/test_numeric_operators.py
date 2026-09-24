"""
Numeric operators (`gt` / `lt`) — coercion is guarded on both sides.

`RuleEvaluator._evaluate` used to call `float(user_value)` unguarded, so a rule
like `age gt 18` against a context of `{"age": "unknown"}` raised `ValueError`
out of the SDK hot path and returned **500**. An operand that will not coerce is
unresolvable, and unresolvable never matches — the same answer the engine gives
a missing attribute or a dangling segment key.

Two layers, and both are load-bearing:

* **Evaluation fails closed** on any operand that is not a number. The user
  context arrives at runtime from the caller's own application, so it can never
  be validated ahead of time.
* **The services reject a non-numeric rule `value` at write time**, so an
  `age gt "eighteen"` typo surfaces as a 400 rather than as a rule that quietly
  matches nobody for the rest of its life.
"""

import pytest
from unittest.mock import patch

from conftest import EnvironmentFlagFactory, VariationFactory
from apps.rules.models import Operator, Rule
from apps.segments.evaluator import SegmentEvaluator
from apps.segments.models import Segment
from apps.targeting.services import RuleEvaluator

SDK_ENDPOINT = "/api/v1/sdk/evaluate/"

_evaluator = RuleEvaluator()


def _rule(operator, value, attribute="age"):
    """A cached-shape rule dict — the form the hot path actually evaluates."""
    return {"attribute": attribute, "operator": operator, "value": value}


# ---------------------------------------------------------------------------
# The bug: a non-numeric operand must not raise
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("operator", [Operator.GT, Operator.LT])
@pytest.mark.parametrize(
    "user_value",
    ["unknown", "", "18 years", "one", "1,8", None, [], {"nested": 1}],
    ids=["word", "empty", "trailing-text", "spelled-out", "comma-decimal",
         "none", "list", "dict"],
)
def test_non_numeric_context_value_does_not_match(operator, user_value):
    """The reported crash: `{"age": "unknown"}` against `age gt 18`."""
    assert _evaluator.matches(_rule(operator, "18"), {"age": user_value}) is False


@pytest.mark.parametrize("operator", [Operator.GT, Operator.LT])
def test_non_numeric_rule_value_does_not_match(operator):
    """Rows written before the write-time check still evaluate, still fail closed."""
    assert _evaluator.matches(_rule(operator, "eighteen"), {"age": "25"}) is False


@pytest.mark.parametrize("operator", [Operator.GT, Operator.LT])
def test_nan_matches_nobody(operator):
    """`nan` coerces but compares False both ways — no operand slips through."""
    assert _evaluator.matches(_rule(operator, "nan"), {"age": "25"}) is False
    assert _evaluator.matches(_rule(operator, "18"), {"age": "nan"}) is False


def test_neither_operator_inverts_an_unresolvable_operand():
    """`gt` and `lt` are opposites, yet both say no to junk.

    If either had been written as `not <the other>`, an unusable attribute
    would match half the users it touched. Nothing here may invert.
    """
    context = {"age": "unknown"}
    assert _evaluator.matches(_rule(Operator.GT, "18"), context) is False
    assert _evaluator.matches(_rule(Operator.LT, "18"), context) is False


# ---------------------------------------------------------------------------
# ...without breaking the comparison itself
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "operator,user_value,rule_value,expected",
    [
        (Operator.GT, "25", "18", True),
        (Operator.GT, "18", "18", False),
        (Operator.GT, "12", "18", False),
        (Operator.LT, "12", "18", True),
        (Operator.LT, "18", "18", False),
        (Operator.LT, "25", "18", False),
        (Operator.GT, "18.5", "18.4", True),
        (Operator.LT, "-5", "0", True),
        (Operator.GT, " 25 ", "18", True),
    ],
)
def test_numeric_comparison_still_works(operator, user_value, rule_value, expected):
    assert _evaluator.matches(_rule(operator, rule_value), {"age": user_value}) is expected


def test_context_value_may_be_a_real_number_not_a_string():
    """SDK callers send JSON, so `age` arrives as an int, not "25"."""
    assert _evaluator.matches(_rule(Operator.GT, "18"), {"age": 25}) is True
    assert _evaluator.matches(_rule(Operator.LT, "18"), {"age": 12.5}) is True


def test_missing_attribute_is_unchanged():
    """Absent attributes were already handled before the operator is reached."""
    assert _evaluator.matches(_rule(Operator.GT, "18"), {"user_id": "u1"}) is False


# ---------------------------------------------------------------------------
# Segment membership takes the same path
# ---------------------------------------------------------------------------

class TestSegmentRulesFailClosedToo:
    """`SegmentEvaluator` delegates to `RuleEvaluator`, so it inherits the fix.

    A crash here would have been worse than in a flag rule: one unusable
    attribute would take down every flag referencing the segment.
    """

    evaluator = SegmentEvaluator()

    @staticmethod
    def _payload(rules):
        return {"included": set(), "excluded": set(), "rules": rules}

    def test_non_numeric_context_leaves_the_user_outside(self):
        payload = self._payload([_rule(Operator.GT, "18")])
        assert self.evaluator.contains(payload, {"user_id": "u1", "age": "unknown"}) is False

    def test_a_numeric_user_still_gets_in(self):
        payload = self._payload([_rule(Operator.GT, "18")])
        assert self.evaluator.contains(payload, {"user_id": "u1", "age": "25"}) is True


# ---------------------------------------------------------------------------
# End to end: the endpoint that returned 500
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestSDKEvaluateSurvivesNonNumericContext:
    """Through HTTP, because a service-level test would not have caught the 500."""

    @pytest.fixture
    def age_gated_flag(self, flag, environment, sdk_key):
        on = VariationFactory(flag=flag, name="on", value_type="boolean", value=True)
        off = VariationFactory(flag=flag, name="off", value_type="boolean", value=False)
        flag.fallthrough_variation, flag.off_variation = on, off
        flag.save(update_fields=["fallthrough_variation", "off_variation"])
        EnvironmentFlagFactory(
            feature_flag=flag, environment=environment,
            is_enabled=True, rollout_percentage=0,
        )
        Rule.objects.create(
            flag=flag, attribute="age", operator=Operator.GT,
            value="18", priority=1, serve_variation=on,
        )
        return flag, sdk_key

    def _evaluate(self, api_client, flag, sdk_key, age):
        with patch("apps.sdk.views.log_evaluation.delay"):
            return api_client.post(
                SDK_ENDPOINT,
                {"flag_key": flag.key, "user_context": {"user_id": "u1", "age": age}},
                format="json",
                HTTP_X_SDK_KEY=sdk_key._full_key,
            )

    def test_non_numeric_attribute_returns_200_and_the_off_variation(
        self, api_client, age_gated_flag
    ):
        flag, sdk_key = age_gated_flag
        resp = self._evaluate(api_client, flag, sdk_key, "unknown")

        assert resp.status_code == 200, "regression: unguarded float() raised a 500 here"
        assert resp.data["result"] is False

    def test_a_numeric_attribute_still_matches_the_rule(self, api_client, age_gated_flag):
        flag, sdk_key = age_gated_flag
        resp = self._evaluate(api_client, flag, sdk_key, "25")

        assert resp.status_code == 200
        assert resp.data["result"] is True


# ---------------------------------------------------------------------------
# Write time: a typo is a 400, not a rule that silently matches nobody
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestNumericRuleValueIsValidatedOnWrite:
    def test_flag_rule_with_a_non_numeric_value_is_rejected(self, auth_client, flag):
        resp = auth_client.post("/api/v1/rules/", {
            "flag": flag.id, "attribute": "age", "operator": "gt",
            "value": "eighteen", "priority": 1,
        }, format="json")

        assert resp.status_code == 400
        assert resp.data["code"] == -418

    def test_flag_rule_with_a_numeric_value_is_accepted(self, auth_client, flag):
        resp = auth_client.post("/api/v1/rules/", {
            "flag": flag.id, "attribute": "age", "operator": "gt",
            "value": "18", "priority": 1,
        }, format="json")

        assert resp.status_code == 201

    def test_string_operators_are_untouched(self, auth_client, flag):
        """Only `gt`/`lt` compare numbers — `eq` still takes any string."""
        resp = auth_client.post("/api/v1/rules/", {
            "flag": flag.id, "attribute": "plan", "operator": "eq",
            "value": "pro", "priority": 1,
        }, format="json")

        assert resp.status_code == 201

    def test_editing_an_unrelated_field_on_a_valid_rule_still_works(self, auth_client, flag):
        """The check reads the merged rule, so a PATCH must not trip over itself."""
        rule = Rule.objects.create(
            flag=flag, attribute="age", operator=Operator.GT, value="18", priority=1,
        )
        resp = auth_client.patch(
            f"/api/v1/rules/{rule.id}/", {"priority": 2}, format="json"
        )

        assert resp.status_code == 200

    def test_patching_a_numeric_rule_to_a_non_numeric_value_is_rejected(
        self, auth_client, flag
    ):
        rule = Rule.objects.create(
            flag=flag, attribute="age", operator=Operator.GT, value="18", priority=1,
        )
        resp = auth_client.patch(
            f"/api/v1/rules/{rule.id}/", {"value": "eighteen"}, format="json"
        )

        assert resp.status_code == 400
        assert resp.data["code"] == -418

    def test_switching_a_string_rule_to_gt_is_rejected(self, auth_client, flag):
        """The operator can change without the value — validate the merged rule."""
        rule = Rule.objects.create(
            flag=flag, attribute="plan", operator=Operator.EQUALS, value="pro", priority=1,
        )
        resp = auth_client.patch(
            f"/api/v1/rules/{rule.id}/", {"operator": "gt"}, format="json"
        )

        assert resp.status_code == 400
        assert resp.data["code"] == -418


@pytest.mark.django_db
class TestSegmentRuleValueIsValidatedOnWrite:
    @pytest.fixture
    def segment(self, project):
        return Segment.objects.create(project=project, key="adults", name="Adults")

    def _url(self, project, segment):
        return f"/api/v1/projects/{project.key}/segments/{segment.key}/rules/"

    def test_non_numeric_value_is_rejected(self, auth_client, project, segment):
        resp = auth_client.post(self._url(project, segment), {
            "attribute": "age", "operator": "gt", "value": "eighteen",
        }, format="json")

        assert resp.status_code == 400
        assert resp.data["code"] == -418

    def test_numeric_value_is_accepted(self, auth_client, project, segment):
        resp = auth_client.post(self._url(project, segment), {
            "attribute": "age", "operator": "gt", "value": "18",
        }, format="json")

        assert resp.status_code == 201
