"""
Segments end-to-end: a flag rule referencing a segment must change what the
SDK serves, and editing the segment must take effect immediately rather than
after the cache TTL.
"""

import pytest
from conftest import EnvironmentFlagFactory, VariationFactory

from apps.core.errors import APIError
from apps.evaluation.services import FlagEvaluationService
from apps.rules.models import Operator, Rule
from apps.rules.services import RuleService
from apps.segments.services import SegmentService

_service = SegmentService()
_rules = RuleService()


@pytest.fixture
def segment(user, project):
    return _service.create_segment(
        project_key=project.key, user=user, key="beta-testers", name="Beta Testers"
    )


@pytest.fixture
def targeted_flag(user, project, flag, environment, segment):
    """Flag on at 0% rollout, with one rule: "in segment beta-testers → on".

    Nobody gets the feature unless the segment lets them in.
    """
    on = VariationFactory(flag=flag, name="on", value_type="boolean", value=True)
    off = VariationFactory(flag=flag, name="off", value_type="boolean", value=False)
    flag.fallthrough_variation, flag.off_variation = on, off
    flag.save(update_fields=["fallthrough_variation", "off_variation"])

    EnvironmentFlagFactory(
        feature_flag=flag, environment=environment,
        is_enabled=True, rollout_percentage=0,
    )
    Rule.objects.create(
        flag=flag, attribute="", operator=Operator.IN_SEGMENT,
        value=segment.key, priority=1, serve_variation=on,
    )
    return flag, environment, segment, on, off


def _evaluate(flag, environment, user_id, **attrs):
    return FlagEvaluationService().evaluate(
        flag_key=flag.key,
        project_id=flag.project_id,
        user_context={"user_id": user_id, **attrs},
        env_id=environment.id,
    ).result


@pytest.mark.django_db
class TestSegmentDrivenEvaluation:
    def test_non_member_does_not_get_the_feature(self, targeted_flag):
        flag, environment, *_ = targeted_flag
        assert _evaluate(flag, environment, "bob") is False

    def test_included_member_gets_the_feature(self, user, project, targeted_flag):
        flag, environment, segment, *_ = targeted_flag
        _service.set_target(
            project_key=project.key, key=segment.key, user=user,
            user_key="alice", excluded=False,
        )
        assert _evaluate(flag, environment, "alice") is True

    def test_rule_based_membership_gets_the_feature(self, user, project, targeted_flag):
        flag, environment, segment, *_ = targeted_flag
        _service.create_rule(
            project_key=project.key, key=segment.key, user=user,
            attribute="plan", operator=Operator.EQUALS, value="pro",
        )
        assert _evaluate(flag, environment, "bob", plan="pro") is True
        assert _evaluate(flag, environment, "carol", plan="free") is False

    def test_exclusion_overrides_rule_membership(self, user, project, targeted_flag):
        flag, environment, segment, *_ = targeted_flag
        _service.create_rule(
            project_key=project.key, key=segment.key, user=user,
            attribute="plan", operator=Operator.EQUALS, value="pro",
        )
        _service.set_target(
            project_key=project.key, key=segment.key, user=user,
            user_key="bob", excluded=True,
        )
        assert _evaluate(flag, environment, "bob", plan="pro") is False

    def test_not_in_segment_rule_inverts_membership(self, user, project, flag, environment, segment):
        on = VariationFactory(flag=flag, name="on", value_type="boolean", value=True)
        off = VariationFactory(flag=flag, name="off", value_type="boolean", value=False)
        flag.fallthrough_variation, flag.off_variation = on, off
        flag.save(update_fields=["fallthrough_variation", "off_variation"])
        EnvironmentFlagFactory(
            feature_flag=flag, environment=environment,
            is_enabled=True, rollout_percentage=0,
        )
        Rule.objects.create(
            flag=flag, attribute="", operator=Operator.NOT_IN_SEGMENT,
            value=segment.key, priority=1, serve_variation=on,
        )
        _service.set_target(
            project_key=project.key, key=segment.key, user=user,
            user_key="alice", excluded=False,
        )
        assert _evaluate(flag, environment, "alice") is False
        assert _evaluate(flag, environment, "bob") is True

    def test_one_segment_serves_two_flags(self, user, project, targeted_flag):
        """The whole point of a segment: define once, reuse."""
        from conftest import FeatureFlagFactory
        flag, environment, segment, *_ = targeted_flag

        second = FeatureFlagFactory(project=project, key="second-flag")
        on2 = VariationFactory(flag=second, name="on", value_type="boolean", value=True)
        off2 = VariationFactory(flag=second, name="off", value_type="boolean", value=False)
        second.fallthrough_variation, second.off_variation = on2, off2
        second.save(update_fields=["fallthrough_variation", "off_variation"])
        EnvironmentFlagFactory(
            feature_flag=second, environment=environment,
            is_enabled=True, rollout_percentage=0,
        )
        Rule.objects.create(
            flag=second, attribute="", operator=Operator.IN_SEGMENT,
            value=segment.key, priority=1, serve_variation=on2,
        )
        _service.set_target(
            project_key=project.key, key=segment.key, user=user,
            user_key="alice", excluded=False,
        )
        # One membership change flipped both flags.
        assert _evaluate(flag, environment, "alice") is True
        assert _evaluate(second, environment, "alice") is True


@pytest.mark.django_db
class TestSegmentEditsInvalidateFlagCaches:
    """A segment edit changes what referencing flags serve. Their cached config
    must be evicted, or the change waits out the full TTL."""

    def test_adding_a_member_takes_effect_immediately(self, user, project, targeted_flag):
        flag, environment, segment, *_ = targeted_flag
        # Prime the cache with the pre-change answer.
        assert _evaluate(flag, environment, "alice") is False
        _service.set_target(
            project_key=project.key, key=segment.key, user=user,
            user_key="alice", excluded=False,
        )
        assert _evaluate(flag, environment, "alice") is True

    def test_removing_a_member_takes_effect_immediately(self, user, project, targeted_flag):
        flag, environment, segment, *_ = targeted_flag
        _service.set_target(
            project_key=project.key, key=segment.key, user=user,
            user_key="alice", excluded=False,
        )
        assert _evaluate(flag, environment, "alice") is True
        _service.remove_target(
            project_key=project.key, key=segment.key, user=user, user_key="alice"
        )
        assert _evaluate(flag, environment, "alice") is False

    def test_adding_a_segment_rule_takes_effect_immediately(self, user, project, targeted_flag):
        flag, environment, segment, *_ = targeted_flag
        assert _evaluate(flag, environment, "bob", plan="pro") is False
        _service.create_rule(
            project_key=project.key, key=segment.key, user=user,
            attribute="plan", operator=Operator.EQUALS, value="pro",
        )
        assert _evaluate(flag, environment, "bob", plan="pro") is True

    def test_deleting_a_segment_rule_takes_effect_immediately(self, user, project, targeted_flag):
        flag, environment, segment, *_ = targeted_flag
        rule = _service.create_rule(
            project_key=project.key, key=segment.key, user=user,
            attribute="plan", operator=Operator.EQUALS, value="pro",
        )
        assert _evaluate(flag, environment, "bob", plan="pro") is True
        _service.delete_rule(
            project_key=project.key, key=segment.key, user=user, rule_id=rule.id
        )
        assert _evaluate(flag, environment, "bob", plan="pro") is False


@pytest.mark.django_db
class TestSegmentRuleReferenceValidation:
    def test_rule_referencing_an_unknown_segment_is_rejected(self, user, flag):
        with pytest.raises(APIError):
            _rules.create(user, {
                "flag": flag, "attribute": "", "operator": Operator.IN_SEGMENT,
                "value": "no-such-segment", "priority": 1,
            })

    def test_rule_referencing_a_real_segment_is_accepted(self, user, flag, segment):
        rule = _rules.create(user, {
            "flag": flag, "attribute": "", "operator": Operator.IN_SEGMENT,
            "value": segment.key, "priority": 1,
        })
        assert rule.value == segment.key

    def test_segment_from_another_project_is_rejected(self, user, flag):
        from conftest import UserFactory, personal_project_for
        foreign_project = personal_project_for(UserFactory())
        foreign_user = foreign_project.organization.memberships.first().user
        foreign_segment = _service.create_segment(
            project_key=foreign_project.key, user=foreign_user,
            key="their-segment", name="Theirs",
        )
        with pytest.raises(APIError):
            _rules.create(user, {
                "flag": flag, "attribute": "", "operator": Operator.IN_SEGMENT,
                "value": foreign_segment.key, "priority": 1,
            })

    def test_a_plain_rule_is_unaffected(self, user, flag):
        rule = _rules.create(user, {
            "flag": flag, "attribute": "plan", "operator": Operator.EQUALS,
            "value": "pro", "priority": 1,
        })
        assert rule.operator == Operator.EQUALS


@pytest.mark.django_db
class TestEvaluationStaysCheap:
    """Segments are resolved when the cache entry is written, never per request."""

    def test_warm_cache_evaluation_hits_the_db_zero_times(
        self, user, project, targeted_flag, django_assert_num_queries
    ):
        flag, environment, segment, *_ = targeted_flag
        _service.create_rule(
            project_key=project.key, key=segment.key, user=user,
            attribute="plan", operator=Operator.EQUALS, value="pro",
        )
        _evaluate(flag, environment, "alice")  # prime

        with django_assert_num_queries(0):
            assert _evaluate(flag, environment, "bob", plan="pro") is True


@pytest.mark.django_db
class TestFanOutDoesNotScaleWithFlagCount:
    """Invalidating referencing flags must not cost a query per flag."""

    @staticmethod
    def _queries_for(user, project, n, key):
        from conftest import FeatureFlagFactory
        from django.db import connection, reset_queries
        from django.test.utils import override_settings

        _service.create_segment(project_key=project.key, user=user, key=key, name="B")
        for i in range(n):
            f = FeatureFlagFactory(project=project, key=f"{key}-flag-{i}")
            Rule.objects.create(
                flag=f, attribute="", operator=Operator.IN_SEGMENT,
                value=key, priority=1,
            )
        with override_settings(DEBUG=True):
            reset_queries()
            _service.set_target(
                project_key=project.key, key=key, user=user,
                user_key="alice", excluded=False,
            )
            return len(connection.queries)

    def test_query_count_is_flat(self, user, project):
        few = self._queries_for(user, project, 2, "seg-few")
        many = self._queries_for(user, project, 20, "seg-many")
        assert few == many, f"fan-out is linear: {few} queries for 2 flags, {many} for 20"
