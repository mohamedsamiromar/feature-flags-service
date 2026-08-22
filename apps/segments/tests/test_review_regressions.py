"""
Regressions for bugs found reviewing the targeting/segments work.

Each test here failed before its fix. Several exercise the HTTP layer
deliberately: the original tests drove the service directly and so never saw
that segment rules were unusable through the serializer.
"""

import pytest
from conftest import EnvironmentFlagFactory, VariationFactory

from apps.core.errors import APIError
from apps.evaluation.services import FlagEvaluationService
from apps.rules.models import Operator, Rule
from apps.segments.evaluator import SegmentEvaluator
from apps.segments.models import SegmentRule
from apps.segments.services import SegmentService

_service = SegmentService()


@pytest.fixture
def segment(user, project):
    return _service.create_segment(
        project_key=project.key, user=user, key="beta", name="Beta"
    )


@pytest.fixture
def seg_base(project):
    return f"/api/v1/projects/{project.key}/segments"


# ---------------------------------------------------------------------------
# 1. Segments must not nest
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestSegmentsDoNotNest:
    """A `not_in_segment` rule *inside* a segment resolved against an empty
    segment map and matched every user — making the segment universal and
    sending every flag referencing it to 100%."""

    def test_service_rejects_a_nested_segment_rule(self, user, project, segment):
        with pytest.raises(APIError):
            _service.create_rule(
                project_key=project.key, key=segment.key, user=user,
                attribute="", operator=Operator.NOT_IN_SEGMENT, value="beta",
            )

    def test_api_rejects_a_nested_segment_rule(self, seg_base, auth_client, segment):
        resp = auth_client.post(
            f"{seg_base}/{segment.key}/rules/",
            {"attribute": "plan", "operator": "not_in_segment", "value": "beta"},
            format="json",
        )
        assert resp.status_code == 400

    def test_update_cannot_smuggle_in_a_segment_operator(self, user, project, segment):
        rule = _service.create_rule(
            project_key=project.key, key=segment.key, user=user,
            attribute="plan", operator=Operator.EQUALS, value="pro",
        )
        with pytest.raises(APIError):
            _service.update_rule(
                project_key=project.key, key=segment.key, user=user,
                rule_id=rule.id, operator=Operator.NOT_IN_SEGMENT,
            )

    def test_evaluator_ignores_a_nested_rule_that_reached_the_db_anyway(self):
        """Belt-and-braces: a hand-written row must not make the segment universal."""
        payload = {
            "included": set(), "excluded": set(),
            "rules": [{"attribute": "", "operator": "not_in_segment", "value": "x"}],
        }
        assert SegmentEvaluator().contains(payload, {"user_id": "anyone"}) is False


# ---------------------------------------------------------------------------
# 2. A dangling segment reference must fail closed
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestUnknownSegmentFailsClosed:
    """`not_in_segment` against an unresolvable key used to invert to True,
    turning a dangling reference into a full rollout."""

    @pytest.fixture
    def flag_with_dangling_rule(self, flag, environment):
        on = VariationFactory(flag=flag, name="on", value_type="boolean", value=True)
        off = VariationFactory(flag=flag, name="off", value_type="boolean", value=False)
        flag.fallthrough_variation, flag.off_variation = on, off
        flag.save(update_fields=["fallthrough_variation", "off_variation"])
        EnvironmentFlagFactory(
            feature_flag=flag, environment=environment,
            is_enabled=True, rollout_percentage=0,
        )
        # Written straight to the DB: the service would reject an unknown key,
        # but a segment deleted out from under an existing rule looks like this.
        Rule.objects.create(
            flag=flag, attribute="", operator=Operator.NOT_IN_SEGMENT,
            value="ghost-segment", priority=1, serve_variation=on,
        )
        return flag, environment

    def test_not_in_segment_does_not_match_everyone(self, flag_with_dangling_rule):
        flag, environment = flag_with_dangling_rule
        result = FlagEvaluationService().evaluate(
            flag_key=flag.key, project_id=flag.project_id,
            user_context={"user_id": "alice"}, env_id=environment.id,
        ).result
        assert result is False

    def test_in_segment_also_does_not_match(self, flag, environment):
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
            value="ghost-segment", priority=1, serve_variation=on,
        )
        result = FlagEvaluationService().evaluate(
            flag_key=flag.key, project_id=flag.project_id,
            user_context={"user_id": "alice"}, env_id=environment.id,
        ).result
        assert result is False


# ---------------------------------------------------------------------------
# 3. Segment rules must be creatable over HTTP
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestSegmentRuleOverHTTP:
    """`Rule.attribute` was required, so `POST /rules/` with `in_segment` and no
    attribute returned 400 — the entire HTTP path for segment rules was dead."""

    def test_create_with_no_attribute_succeeds(self, auth_client, flag, segment):
        resp = auth_client.post("/api/v1/rules/", {
            "flag": flag.id, "operator": "in_segment",
            "value": segment.key, "priority": 1,
        }, format="json")
        assert resp.status_code == 201

    def test_create_with_blank_attribute_succeeds(self, auth_client, flag, segment):
        resp = auth_client.post("/api/v1/rules/", {
            "flag": flag.id, "attribute": "", "operator": "not_in_segment",
            "value": segment.key, "priority": 1,
        }, format="json")
        assert resp.status_code == 201

    def test_unknown_segment_still_rejected_over_http(self, auth_client, flag):
        resp = auth_client.post("/api/v1/rules/", {
            "flag": flag.id, "operator": "in_segment",
            "value": "no-such-segment", "priority": 1,
        }, format="json")
        assert resp.status_code == 400

    def test_non_segment_rule_still_requires_an_attribute(self, auth_client, flag):
        """Relaxing the field must not let a meaningless `eq` rule through."""
        resp = auth_client.post("/api/v1/rules/", {
            "flag": flag.id, "operator": "eq", "value": "pro", "priority": 1,
        }, format="json")
        assert resp.status_code == 400

    def test_non_segment_rule_with_blank_attribute_is_rejected(self, auth_client, flag):
        resp = auth_client.post("/api/v1/rules/", {
            "flag": flag.id, "attribute": "", "operator": "eq",
            "value": "pro", "priority": 1,
        }, format="json")
        assert resp.status_code == 400

    def test_segment_rule_created_over_http_actually_evaluates(
        self, auth_client, user, project, flag, environment, segment
    ):
        """End-to-end through the serializer, not the service."""
        on = VariationFactory(flag=flag, name="on", value_type="boolean", value=True)
        off = VariationFactory(flag=flag, name="off", value_type="boolean", value=False)
        flag.fallthrough_variation, flag.off_variation = on, off
        flag.save(update_fields=["fallthrough_variation", "off_variation"])
        EnvironmentFlagFactory(
            feature_flag=flag, environment=environment,
            is_enabled=True, rollout_percentage=0,
        )
        created = auth_client.post("/api/v1/rules/", {
            "flag": flag.id, "operator": "in_segment", "value": segment.key,
            "priority": 1, "serve_variation": on.id,
        }, format="json")
        assert created.status_code == 201

        auth_client.put(
            f"/api/v1/projects/{project.key}/segments/{segment.key}/targets/",
            {"user_key": "alice", "excluded": False}, format="json",
        )
        result = FlagEvaluationService().evaluate(
            flag_key=flag.key, project_id=flag.project_id,
            user_context={"user_id": "alice"}, env_id=environment.id,
        ).result
        assert result is True


# ---------------------------------------------------------------------------
# 4. A plain segment rule still works over HTTP
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestSegmentOwnRulesOverHTTP:
    def test_post_attribute_rule_succeeds(self, seg_base, auth_client, segment):
        resp = auth_client.post(
            f"{seg_base}/{segment.key}/rules/",
            {"attribute": "plan", "operator": "eq", "value": "pro"}, format="json",
        )
        assert resp.status_code == 201
        assert SegmentRule.objects.filter(segment=segment, attribute="plan").exists()
