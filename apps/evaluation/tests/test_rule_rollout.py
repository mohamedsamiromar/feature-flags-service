"""
Rule-level percentage rollout.

A matched rule serves its variation to only `rollout_percentage` of the users
it matches — "roll this out to 20% of beta testers". The rule still wins
outright: users it matches but does not bucket in get the off variation rather
than falling through to a later rule.
"""

import pytest
from conftest import EnvironmentFlagFactory, VariationFactory

from apps.evaluation.services import FlagEvaluationService
from apps.rules.models import Operator, Rule


@pytest.fixture
def rollout_flag(flag, environment):
    """Flag on, flag-level rollout 0 — only a rule can let anyone through."""
    on = VariationFactory(flag=flag, name="on", value_type="boolean", value=True)
    off = VariationFactory(flag=flag, name="off", value_type="boolean", value=False)
    flag.fallthrough_variation, flag.off_variation = on, off
    flag.save(update_fields=["fallthrough_variation", "off_variation"])
    EnvironmentFlagFactory(
        feature_flag=flag, environment=environment,
        is_enabled=True, rollout_percentage=0,
    )
    return flag, environment, on, off


def _evaluate(flag, environment, user_id, **attrs):
    return FlagEvaluationService().evaluate(
        flag_key=flag.key, project_id=flag.project_id,
        user_context={"user_id": user_id, **attrs}, env_id=environment.id,
    ).result


def _served_count(flag, environment, n=400, **attrs):
    return sum(
        1 for i in range(n)
        if _evaluate(flag, environment, f"user-{i}", **attrs) is True
    )


@pytest.mark.django_db
class TestRuleRolloutBoundaries:
    def test_default_serves_every_matched_user(self, rollout_flag):
        """An existing rule keeps applying to everyone it matches."""
        flag, environment, on, _ = rollout_flag
        rule = Rule.objects.create(
            flag=flag, attribute="plan", operator=Operator.EQUALS,
            value="pro", priority=1, serve_variation=on,
        )
        assert rule.rollout_percentage == 100
        assert _served_count(flag, environment, plan="pro") == 400

    def test_zero_serves_nobody_it_matches(self, rollout_flag):
        flag, environment, on, _ = rollout_flag
        Rule.objects.create(
            flag=flag, attribute="plan", operator=Operator.EQUALS,
            value="pro", priority=1, serve_variation=on, rollout_percentage=0,
        )
        assert _served_count(flag, environment, plan="pro") == 0

    def test_partial_rollout_serves_roughly_that_share(self, rollout_flag):
        flag, environment, on, _ = rollout_flag
        Rule.objects.create(
            flag=flag, attribute="plan", operator=Operator.EQUALS,
            value="pro", priority=1, serve_variation=on, rollout_percentage=50,
        )
        served = _served_count(flag, environment, n=400, plan="pro")
        assert 150 < served < 250, f"expected ~200 of 400, got {served}"

    def test_unmatched_users_are_unaffected_by_the_rollout(self, rollout_flag):
        flag, environment, on, _ = rollout_flag
        Rule.objects.create(
            flag=flag, attribute="plan", operator=Operator.EQUALS,
            value="pro", priority=1, serve_variation=on, rollout_percentage=100,
        )
        # Does not match the rule at all, so falls to the flag's 0% rollout.
        assert _evaluate(flag, environment, "u1", plan="free") is False


@pytest.mark.django_db
class TestRuleRolloutIsDeterministic:
    def test_same_user_always_gets_the_same_answer(self, rollout_flag):
        flag, environment, on, _ = rollout_flag
        Rule.objects.create(
            flag=flag, attribute="plan", operator=Operator.EQUALS,
            value="pro", priority=1, serve_variation=on, rollout_percentage=50,
        )
        first = [_evaluate(flag, environment, f"user-{i}", plan="pro") for i in range(50)]
        second = [_evaluate(flag, environment, f"user-{i}", plan="pro") for i in range(50)]
        assert first == second

    def test_two_rules_at_the_same_share_pick_different_users(self, rollout_flag):
        """Bucketing is salted per rule.

        Both rules live on the SAME flag at the same percentage, so flag_key
        and user_id are identical between them — the rule id is the only thing
        separating the two slices. Put these on different flags and the flag
        key would separate them on its own, and the test would pass with no
        salt at all.
        """
        flag, environment, on, _ = rollout_flag
        other = VariationFactory(flag=flag, name="other", value_type="boolean", value=True)
        Rule.objects.create(
            flag=flag, attribute="plan", operator=Operator.EQUALS,
            value="pro", priority=1, serve_variation=on, rollout_percentage=20,
        )
        Rule.objects.create(
            flag=flag, attribute="plan", operator=Operator.EQUALS,
            value="team", priority=2, serve_variation=other, rollout_percentage=20,
        )

        pro_slice = {
            i for i in range(400)
            if _evaluate(flag, environment, f"user-{i}", plan="pro") is True
        }
        team_slice = {
            i for i in range(400)
            if _evaluate(flag, environment, f"user-{i}", plan="team") is True
        }

        assert pro_slice and team_slice, "both rules should serve someone"
        assert pro_slice != team_slice, (
            "two rules at 20% on the same flag selected an identical slice - "
            "the per-rule salt is not being applied"
        )


@pytest.mark.django_db
class TestMatchedRuleWinsOutright:
    def test_out_of_bucket_does_not_fall_through_to_a_later_rule(self, rollout_flag):
        """A matched rule is the decision. A user it matches but does not bucket
        in must get the off variation, not a second rule's variation."""
        flag, environment, on, off = rollout_flag
        other = VariationFactory(flag=flag, name="other", value_type="string", value="second-rule")
        Rule.objects.create(
            flag=flag, attribute="plan", operator=Operator.EQUALS,
            value="pro", priority=1, serve_variation=on, rollout_percentage=0,
        )
        Rule.objects.create(
            flag=flag, attribute="plan", operator=Operator.EQUALS,
            value="pro", priority=2, serve_variation=other, rollout_percentage=100,
        )
        results = {_evaluate(flag, environment, f"user-{i}", plan="pro") for i in range(30)}
        assert results == {False}, f"later rule leaked through: {results}"


@pytest.mark.django_db
class TestRuleRolloutWithSegments:
    def test_rolls_out_to_a_share_of_a_segment(self, user, project, rollout_flag):
        """The headline use case: 20% of the beta-testers segment."""
        from apps.segments.services import SegmentService

        flag, environment, on, _ = rollout_flag
        segments = SegmentService()
        segment = segments.create_segment(
            project_key=project.key, user=user, key="beta", name="Beta"
        )
        segments.create_rule(
            project_key=project.key, key=segment.key, user=user,
            attribute="plan", operator=Operator.EQUALS, value="pro",
        )
        Rule.objects.create(
            flag=flag, attribute="", operator=Operator.IN_SEGMENT,
            value=segment.key, priority=1, serve_variation=on,
            rollout_percentage=50,
        )
        in_segment = _served_count(flag, environment, n=300, plan="pro")
        assert 100 < in_segment < 200, f"expected ~150 of 300, got {in_segment}"
        # Someone outside the segment gets nothing regardless of bucketing.
        assert _evaluate(flag, environment, "outsider", plan="free") is False


@pytest.mark.django_db
class TestStaleCacheEntries:
    def test_payload_without_a_rollout_key_serves_everyone(self, rollout_flag):
        """A cached entry written before this field existed outlives the deploy
        by up to the TTL. A missing key must mean 100, never 0."""
        from django.core.cache import cache

        flag, environment, on, _ = rollout_flag
        Rule.objects.create(
            flag=flag, attribute="plan", operator=Operator.EQUALS,
            value="pro", priority=1, serve_variation=on,
        )
        _evaluate(flag, environment, "warm", plan="pro")  # prime

        key = f"flags:{flag.project_id}:{environment.id}:{flag.key}"
        payload = cache.get(key)
        for rule in payload["rules"]:
            rule.pop("rollout_percentage", None)
            rule.pop("id", None)
        cache.set(key, payload, 300)

        assert _evaluate(flag, environment, "someone", plan="pro") is True


@pytest.mark.django_db
class TestThreeLayerValidation:
    """Matching FeatureFlag.rollout_percentage: serializer, model, and DB."""

    def test_serializer_rejects_out_of_range(self, auth_client, flag):
        resp = auth_client.post("/api/v1/rules/", {
            "flag": flag.id, "attribute": "plan", "operator": "eq",
            "value": "pro", "priority": 1, "rollout_percentage": 150,
        }, format="json")
        assert resp.status_code == 400

    def test_model_validator_rejects_out_of_range(self, flag):
        from django.core.exceptions import ValidationError

        rule = Rule(
            flag=flag, attribute="plan", operator=Operator.EQUALS,
            value="pro", priority=1, rollout_percentage=150,
        )
        with pytest.raises(ValidationError):
            rule.full_clean()

    def test_db_constraint_rejects_out_of_range(self, flag):
        from django.db import IntegrityError, transaction

        with pytest.raises(IntegrityError):
            with transaction.atomic():
                Rule.objects.create(
                    flag=flag, attribute="plan", operator=Operator.EQUALS,
                    value="pro", priority=1, rollout_percentage=150,
                )

    def test_valid_value_is_accepted_over_http(self, auth_client, flag):
        resp = auth_client.post("/api/v1/rules/", {
            "flag": flag.id, "attribute": "plan", "operator": "eq",
            "value": "pro", "priority": 1, "rollout_percentage": 25,
        }, format="json")
        assert resp.status_code == 201
        assert resp.json()["rollout_percentage"] == 25
