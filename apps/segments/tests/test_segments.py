"""
Reusable segments — model, service, API, and cache-invalidation tests.

A segment names a group of users once so many flags can target it. The
interesting behaviour is (a) membership precedence — exclude beats include
beats rules — and (b) that editing a segment invalidates every flag whose
rules reference it.
"""

import pytest
from conftest import FeatureFlagFactory, UserFactory, personal_project_for

from apps.audit.models import AuditLog
from apps.audit.services import AuditService
from apps.core.errors import APIError
from apps.rules.models import Operator, Rule
from apps.segments.evaluator import SegmentEvaluator
from apps.segments.models import Segment, SegmentRule, SegmentTarget
from apps.segments.queries import SegmentQuery
from apps.segments.services import SegmentService

_service = SegmentService()


@pytest.fixture
def segment(user, project):
    return _service.create_segment(
        project_key=project.key, user=user, key="beta-testers", name="Beta Testers"
    )


@pytest.fixture
def seg_base(project):
    return f"/api/v1/projects/{project.key}/segments"


# ---------------------------------------------------------------------------
# Membership precedence — the heart of the feature
# ---------------------------------------------------------------------------

class TestSegmentEvaluator:
    """Pure-logic tests: no DB, operating on the cached payload shape."""

    evaluator = SegmentEvaluator()

    @staticmethod
    def _payload(included=(), excluded=(), rules=()):
        return {"included": set(included), "excluded": set(excluded), "rules": list(rules)}

    def test_included_user_is_a_member(self):
        payload = self._payload(included=["alice"])
        assert self.evaluator.contains(payload, {"user_id": "alice"}) is True

    def test_unlisted_user_is_not_a_member(self):
        payload = self._payload(included=["alice"])
        assert self.evaluator.contains(payload, {"user_id": "bob"}) is False

    def test_rule_match_admits_a_user(self):
        payload = self._payload(rules=[{"attribute": "plan", "operator": "eq", "value": "pro"}])
        assert self.evaluator.contains(payload, {"user_id": "bob", "plan": "pro"}) is True

    def test_rules_are_ored_not_anded(self):
        payload = self._payload(rules=[
            {"attribute": "plan", "operator": "eq", "value": "pro"},
            {"attribute": "country", "operator": "eq", "value": "EG"},
        ])
        # Matches the second rule only — still a member.
        assert self.evaluator.contains(payload, {"user_id": "bob", "country": "EG"}) is True

    def test_exclusion_beats_a_matching_rule(self):
        payload = self._payload(
            excluded=["bob"],
            rules=[{"attribute": "plan", "operator": "eq", "value": "pro"}],
        )
        assert self.evaluator.contains(payload, {"user_id": "bob", "plan": "pro"}) is False

    def test_exclusion_beats_inclusion(self):
        payload = self._payload(included=["bob"], excluded=["bob"])
        assert self.evaluator.contains(payload, {"user_id": "bob"}) is False

    def test_inclusion_admits_a_user_matching_no_rule(self):
        payload = self._payload(
            included=["alice"],
            rules=[{"attribute": "plan", "operator": "eq", "value": "pro"}],
        )
        assert self.evaluator.contains(payload, {"user_id": "alice", "plan": "free"}) is True

    def test_empty_segment_matches_nobody(self):
        """An unconfigured segment must never silently match everyone."""
        assert self.evaluator.contains(self._payload(), {"user_id": "alice"}) is False


# ---------------------------------------------------------------------------
# Service — CRUD, membership, RBAC
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestSegmentCRUD:
    def test_create_persists(self, user, project):
        segment = _service.create_segment(
            project_key=project.key, user=user, key="pro-users", name="Pro Users"
        )
        assert Segment.objects.filter(project=project, key="pro-users").exists()
        assert segment.name == "Pro Users"

    def test_duplicate_key_in_same_project_is_rejected(self, user, project, segment):
        with pytest.raises(APIError):
            _service.create_segment(
                project_key=project.key, user=user, key="beta-testers", name="Dupe"
            )

    def test_same_key_in_another_project_is_allowed(self, user, segment):
        other_project = personal_project_for(UserFactory())
        other_user = other_project.organization.memberships.first().user
        created = _service.create_segment(
            project_key=other_project.key, user=other_user,
            key="beta-testers", name="Beta Testers",
        )
        assert created.key == "beta-testers"

    def test_update_changes_name(self, user, project, segment):
        updated = _service.update_segment(
            project_key=project.key, segment_key=segment.key, user=user, name="Renamed"
        )
        assert updated.name == "Renamed"

    def test_key_is_frozen_after_create(self, user, project, segment):
        """Rules reference a segment by key — changing it would orphan them."""
        with pytest.raises(APIError):
            _service.update_segment(
                project_key=project.key, segment_key=segment.key, user=user,
                key="new-key", name="Renamed",
            )
        segment.refresh_from_db()
        assert segment.key == "beta-testers"

    def test_resending_the_same_key_is_not_an_error(self, user, project, segment):
        """A dashboard PATCHing the whole object back must not be rejected."""
        updated = _service.update_segment(
            project_key=project.key, segment_key=segment.key, user=user,
            key=segment.key, name="Renamed",
        )
        assert updated.name == "Renamed"

    def test_delete_removes_it(self, user, project, segment):
        _service.delete_segment(project_key=project.key, key=segment.key, user=user)
        assert not Segment.objects.filter(pk=segment.pk).exists()

    def test_non_member_cannot_read(self, project, segment):
        with pytest.raises(APIError):
            _service.get_segment(
                project_key=project.key, user=UserFactory(), key=segment.key
            )

    def test_non_member_cannot_create(self, project):
        with pytest.raises(APIError):
            _service.create_segment(
                project_key=project.key, user=UserFactory(), key="x", name="X"
            )


@pytest.mark.django_db
class TestSegmentDeletionGuard:
    def test_delete_is_refused_while_a_rule_references_it(self, user, project, segment, flag):
        Rule.objects.create(
            flag=flag, attribute="", operator=Operator.IN_SEGMENT,
            value=segment.key, priority=1,
        )
        with pytest.raises(APIError):
            _service.delete_segment(project_key=project.key, key=segment.key, user=user)
        assert Segment.objects.filter(pk=segment.pk).exists()

    def test_delete_succeeds_once_the_rule_is_gone(self, user, project, segment, flag):
        rule = Rule.objects.create(
            flag=flag, attribute="", operator=Operator.IN_SEGMENT,
            value=segment.key, priority=1,
        )
        rule.delete()
        _service.delete_segment(project_key=project.key, key=segment.key, user=user)
        assert not Segment.objects.filter(pk=segment.pk).exists()


@pytest.mark.django_db
class TestSegmentMembers:
    def test_set_target_includes_a_user(self, user, project, segment):
        target, created = _service.set_target(
            project_key=project.key, key=segment.key, user=user,
            user_key="alice", excluded=False,
        )
        assert created is True
        assert target.excluded is False

    def test_moving_a_user_to_excluded_updates_in_place(self, user, project, segment):
        _service.set_target(
            project_key=project.key, key=segment.key, user=user,
            user_key="alice", excluded=False,
        )
        target, created = _service.set_target(
            project_key=project.key, key=segment.key, user=user,
            user_key="alice", excluded=True,
        )
        assert created is False
        assert target.excluded is True
        # A user is never both included and excluded.
        assert SegmentTarget.objects.filter(segment=segment, user_key="alice").count() == 1

    def test_remove_target(self, user, project, segment):
        _service.set_target(
            project_key=project.key, key=segment.key, user=user,
            user_key="alice", excluded=False,
        )
        _service.remove_target(
            project_key=project.key, key=segment.key, user=user, user_key="alice"
        )
        assert not SegmentTarget.objects.filter(segment=segment, user_key="alice").exists()

    def test_remove_unknown_target_raises(self, user, project, segment):
        with pytest.raises(APIError):
            _service.remove_target(
                project_key=project.key, key=segment.key, user=user, user_key="nobody"
            )


@pytest.mark.django_db
class TestSegmentRules:
    def test_create_rule(self, user, project, segment):
        rule = _service.create_rule(
            project_key=project.key, key=segment.key, user=user,
            attribute="plan", operator=Operator.EQUALS, value="pro",
        )
        assert SegmentRule.objects.filter(pk=rule.pk, segment=segment).exists()

    def test_update_rule(self, user, project, segment):
        rule = _service.create_rule(
            project_key=project.key, key=segment.key, user=user,
            attribute="plan", operator=Operator.EQUALS, value="pro",
        )
        updated = _service.update_rule(
            project_key=project.key, key=segment.key, user=user,
            rule_id=rule.id, value="enterprise",
        )
        assert updated.value == "enterprise"

    def test_delete_rule(self, user, project, segment):
        rule = _service.create_rule(
            project_key=project.key, key=segment.key, user=user,
            attribute="plan", operator=Operator.EQUALS, value="pro",
        )
        _service.delete_rule(
            project_key=project.key, key=segment.key, user=user, rule_id=rule.id
        )
        assert not SegmentRule.objects.filter(pk=rule.pk).exists()

    def test_rule_from_another_segment_is_not_reachable(self, user, project, segment):
        other = _service.create_segment(
            project_key=project.key, user=user, key="other", name="Other"
        )
        rule = _service.create_rule(
            project_key=project.key, key=other.key, user=user,
            attribute="plan", operator=Operator.EQUALS, value="pro",
        )
        with pytest.raises(APIError):
            _service.delete_rule(
                project_key=project.key, key=segment.key, user=user, rule_id=rule.id
            )


@pytest.mark.django_db
class TestSegmentAuditTrail:
    def test_create_is_audited(self, user, project):
        _service.create_segment(
            project_key=project.key, user=user, key="audited", name="Audited"
        )
        log = AuditLog.objects.get(entity_type="segment", action=AuditService.CREATE)
        assert log.new_value["key"] == "audited"

    def test_member_change_is_audited(self, user, project, segment):
        _service.set_target(
            project_key=project.key, key=segment.key, user=user,
            user_key="alice", excluded=False,
        )
        assert AuditLog.objects.filter(entity_type="segmenttarget").exists()

    def test_delete_keeps_entity_id(self, user, project, segment):
        pk = segment.pk
        _service.delete_segment(project_key=project.key, key=segment.key, user=user)
        log = AuditLog.objects.get(entity_type="segment", action=AuditService.DELETE)
        assert log.entity_id == str(pk)


# ---------------------------------------------------------------------------
# The reverse lookup that keeps caches honest
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestReferencingFlags:
    def test_finds_flags_whose_rules_reference_the_segment(self, project, segment, flag):
        Rule.objects.create(
            flag=flag, attribute="", operator=Operator.IN_SEGMENT,
            value=segment.key, priority=1,
        )
        assert list(SegmentQuery.referencing_flags(segment)) == [flag]

    def test_ignores_flags_referencing_a_different_segment(self, user, project, segment, flag):
        other = _service.create_segment(
            project_key=project.key, user=user, key="other", name="Other"
        )
        Rule.objects.create(
            flag=flag, attribute="", operator=Operator.IN_SEGMENT,
            value=other.key, priority=1,
        )
        assert list(SegmentQuery.referencing_flags(segment)) == []

    def test_ignores_a_non_segment_rule_whose_value_matches_the_key(self, project, segment, flag):
        """A plain `eq` rule on the literal string "beta-testers" is not a reference."""
        Rule.objects.create(
            flag=flag, attribute="plan", operator=Operator.EQUALS,
            value=segment.key, priority=1,
        )
        assert list(SegmentQuery.referencing_flags(segment)) == []

    def test_does_not_cross_project_boundaries(self, project, segment):
        """Another project's flag with the same segment key must not be touched."""
        foreign_flag = FeatureFlagFactory()
        Rule.objects.create(
            flag=foreign_flag, attribute="", operator=Operator.IN_SEGMENT,
            value=segment.key, priority=1,
        )
        assert list(SegmentQuery.referencing_flags(segment)) == []


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestSegmentAPI:
    def test_create_returns_201(self, seg_base, auth_client):
        resp = auth_client.post(
            f"{seg_base}/", {"key": "pro", "name": "Pro Users"}, format="json"
        )
        assert resp.status_code == 201
        assert resp.json()["key"] == "pro"

    def test_duplicate_key_returns_409(self, seg_base, auth_client, segment):
        resp = auth_client.post(
            f"{seg_base}/", {"key": "beta-testers", "name": "Dupe"}, format="json"
        )
        assert resp.status_code == 409

    def test_list_returns_segments(self, seg_base, auth_client, segment):
        resp = auth_client.get(f"{seg_base}/")
        assert resp.status_code == 200
        assert [row["key"] for row in resp.json()] == ["beta-testers"]

    def test_retrieve_embeds_members_and_rules(self, seg_base, auth_client, segment, user, project):
        _service.set_target(
            project_key=project.key, key=segment.key, user=user,
            user_key="alice", excluded=False,
        )
        _service.create_rule(
            project_key=project.key, key=segment.key, user=user,
            attribute="plan", operator=Operator.EQUALS, value="pro",
        )
        resp = auth_client.get(f"{seg_base}/{segment.key}/")
        body = resp.json()
        assert [t["user_key"] for t in body["targets"]] == ["alice"]
        assert [r["attribute"] for r in body["rules"]] == ["plan"]

    def test_put_target_returns_201_then_200(self, seg_base, auth_client, segment):
        first = auth_client.put(
            f"{seg_base}/{segment.key}/targets/",
            {"user_key": "alice", "excluded": False}, format="json",
        )
        second = auth_client.put(
            f"{seg_base}/{segment.key}/targets/",
            {"user_key": "alice", "excluded": True}, format="json",
        )
        assert first.status_code == 201
        assert second.status_code == 200
        assert second.json()["excluded"] is True

    def test_delete_target_returns_204(self, seg_base, auth_client, segment, user, project):
        _service.set_target(
            project_key=project.key, key=segment.key, user=user,
            user_key="alice", excluded=False,
        )
        resp = auth_client.delete(f"{seg_base}/{segment.key}/targets/alice/")
        assert resp.status_code == 204

    def test_post_rule_returns_201(self, seg_base, auth_client, segment):
        resp = auth_client.post(
            f"{seg_base}/{segment.key}/rules/",
            {"attribute": "plan", "operator": "eq", "value": "pro"}, format="json",
        )
        assert resp.status_code == 201

    def test_delete_referenced_segment_returns_409(self, seg_base, auth_client, segment, flag):
        Rule.objects.create(
            flag=flag, attribute="", operator=Operator.IN_SEGMENT,
            value=segment.key, priority=1,
        )
        resp = auth_client.delete(f"{seg_base}/{segment.key}/")
        assert resp.status_code == 409

    def test_another_projects_segment_is_invisible(self, seg_base, auth_client):
        foreign_project = personal_project_for(UserFactory())
        foreign_user = foreign_project.organization.memberships.first().user
        _service.create_segment(
            project_key=foreign_project.key, user=foreign_user, key="secret", name="Secret"
        )
        resp = auth_client.get(f"{seg_base}/secret/")
        assert resp.status_code == 404

    def test_unknown_project_returns_404(self, auth_client):
        resp = auth_client.get("/api/v1/projects/no-such-project/segments/")
        assert resp.status_code == 404
