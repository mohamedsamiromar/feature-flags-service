"""
Individual user targeting — service, API, and evaluation tests.

A target pins one `user_key` to one variation of a flag, overriding targeting
rules and the percentage rollout for that user only. Targeting the true /
fallthrough variation is an allowlist; targeting the false / off variation is
a denylist.
"""

import pytest
from conftest import (
    EnvironmentFlagFactory,
    FeatureFlagFactory,
    UserFactory,
    VariationFactory,
    personal_project_for,
)
from apps.audit.models import AuditLog
from apps.audit.services import AuditService
from apps.core.errors import APIError
from apps.evaluation.services import FlagEvaluationService
from apps.flags.models import FlagTarget
from apps.flags.services import FlagService

_service = FlagService()


@pytest.fixture
def variations(flag):
    """A true/false pair on the shared flag, wired as fallthrough/off."""
    on = VariationFactory(flag=flag, name="on", value_type="boolean", value=True)
    off = VariationFactory(flag=flag, name="off", value_type="boolean", value=False)
    flag.fallthrough_variation = on
    flag.off_variation = off
    flag.save(update_fields=["fallthrough_variation", "off_variation"])
    return on, off


# ---------------------------------------------------------------------------
# Service tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestSetTarget:
    def test_creates_target(self, user, flag, variations):
        on, _ = variations
        target, created = _service.set_target(
            project_key=flag.project.key, key=flag.key, user=user,
            user_key="alice", variation_id=on.id,
        )
        assert created is True
        assert target.user_key == "alice"
        assert target.variation_id == on.id

    def test_retargeting_same_user_updates_in_place(self, user, flag, variations):
        on, off = variations
        _service.set_target(
            project_key=flag.project.key, key=flag.key, user=user,
            user_key="alice", variation_id=on.id,
        )
        target, created = _service.set_target(
            project_key=flag.project.key, key=flag.key, user=user,
            user_key="alice", variation_id=off.id,
        )
        assert created is False
        assert target.variation_id == off.id
        # One user holds at most one target per flag.
        assert FlagTarget.objects.filter(flag=flag, user_key="alice").count() == 1

    def test_variation_from_another_flag_is_rejected(self, user, flag, variations):
        foreign = VariationFactory(
            flag=FeatureFlagFactory(project=flag.project),
            name="x", value_type="boolean", value=True,
        )
        with pytest.raises(APIError):
            _service.set_target(
                project_key=flag.project.key, key=flag.key, user=user,
                user_key="alice", variation_id=foreign.id,
            )

    def test_archived_flag_is_rejected(self, user, flag, variations):
        on, _ = variations
        _service.archive_flag(project_key=flag.project.key, key=flag.key, user=user)
        with pytest.raises(APIError):
            _service.set_target(
                project_key=flag.project.key, key=flag.key, user=user,
                user_key="alice", variation_id=on.id,
            )

    def test_non_member_cannot_target(self, flag, variations):
        on, _ = variations
        with pytest.raises(APIError):
            _service.set_target(
                project_key=flag.project.key, key=flag.key, user=UserFactory(),
                user_key="alice", variation_id=on.id,
            )


@pytest.mark.django_db
class TestRemoveTarget:
    def test_removes_row(self, user, flag, variations):
        on, _ = variations
        _service.set_target(
            project_key=flag.project.key, key=flag.key, user=user,
            user_key="alice", variation_id=on.id,
        )
        _service.remove_target(
            project_key=flag.project.key, key=flag.key, user=user, user_key="alice"
        )
        assert not FlagTarget.objects.filter(flag=flag, user_key="alice").exists()

    def test_unknown_user_key_raises(self, user, flag, variations):
        with pytest.raises(APIError):
            _service.remove_target(
                project_key=flag.project.key, key=flag.key, user=user, user_key="nobody"
            )


@pytest.mark.django_db
class TestTargetCascades:
    def test_deleting_the_variation_drops_its_targets(self, user, flag, variations):
        on, _ = variations
        _service.set_target(
            project_key=flag.project.key, key=flag.key, user=user,
            user_key="alice", variation_id=on.id,
        )
        _service.delete_variation(
            project_key=flag.project.key, key=flag.key, user=user, variation_id=on.id
        )
        # A target can never point at a variation that no longer exists.
        assert not FlagTarget.objects.filter(flag=flag, user_key="alice").exists()


@pytest.mark.django_db
class TestTargetAuditTrail:
    @staticmethod
    def _logs():
        return AuditLog.objects.filter(entity_type="flagtarget")

    def test_create_is_audited_as_create(self, user, flag, variations):
        on, _ = variations
        _service.set_target(
            project_key=flag.project.key, key=flag.key, user=user,
            user_key="alice", variation_id=on.id,
        )
        log = self._logs().get(action=AuditService.CREATE)
        assert log.old_value is None
        assert log.new_value["user_key"] == "alice"

    def test_retarget_is_audited_as_update_with_before_state(self, user, flag, variations):
        on, off = variations
        _service.set_target(
            project_key=flag.project.key, key=flag.key, user=user,
            user_key="alice", variation_id=on.id,
        )
        _service.set_target(
            project_key=flag.project.key, key=flag.key, user=user,
            user_key="alice", variation_id=off.id,
        )
        log = self._logs().get(action=AuditService.UPDATE)
        assert log.old_value["variation"] == on.id
        assert log.new_value["variation"] == off.id

    def test_remove_is_audited_with_entity_id(self, user, flag, variations):
        on, _ = variations
        target, _created = _service.set_target(
            project_key=flag.project.key, key=flag.key, user=user,
            user_key="alice", variation_id=on.id,
        )
        pk = target.pk
        _service.remove_target(
            project_key=flag.project.key, key=flag.key, user=user, user_key="alice"
        )
        log = self._logs().get(action=AuditService.DELETE)
        assert log.entity_id == str(pk)
        assert log.new_value is None


# ---------------------------------------------------------------------------
# Evaluation tests — where targeting actually earns its keep
# ---------------------------------------------------------------------------

@pytest.fixture
def eval_setup(flag, environment, variations):
    """Flag on in `environment` at 0% rollout: nobody gets it by default."""
    on, off = variations
    EnvironmentFlagFactory(
        feature_flag=flag, environment=environment,
        is_enabled=True, rollout_percentage=0,
    )
    return flag, environment, on, off


def _evaluate(flag, environment, user_key):
    return FlagEvaluationService().evaluate(
        flag_key=flag.key,
        project_id=flag.project_id,
        user_context={"user_id": user_key},
        env_id=environment.id,
    )


@pytest.mark.django_db
class TestTargetedEvaluation:
    def test_untargeted_user_stays_out_at_zero_percent(self, eval_setup):
        flag, environment, _on, _off = eval_setup
        assert _evaluate(flag, environment, "bob").result is False

    def test_allowlisted_user_gets_the_feature(self, user, eval_setup):
        flag, environment, on, _off = eval_setup
        _service.set_target(
            project_key=flag.project.key, key=flag.key, user=user,
            user_key="alice", variation_id=on.id,
        )
        assert _evaluate(flag, environment, "alice").result is True

    def test_target_does_not_leak_to_other_users(self, user, eval_setup):
        flag, environment, on, _off = eval_setup
        _service.set_target(
            project_key=flag.project.key, key=flag.key, user=user,
            user_key="alice", variation_id=on.id,
        )
        assert _evaluate(flag, environment, "bob").result is False

    def test_denylisted_user_held_out_of_full_rollout(self, user, flag, environment, variations):
        on, off = variations
        EnvironmentFlagFactory(
            feature_flag=flag, environment=environment,
            is_enabled=True, rollout_percentage=100,
        )
        _service.set_target(
            project_key=flag.project.key, key=flag.key, user=user,
            user_key="alice", variation_id=off.id,
        )
        assert _evaluate(flag, environment, "bob").result is True
        assert _evaluate(flag, environment, "alice").result is False

    def test_kill_switch_beats_a_target(self, user, flag, environment, variations):
        """A flag that is off serves the off variation to everyone, targets included."""
        on, _off = variations
        EnvironmentFlagFactory(
            feature_flag=flag, environment=environment,
            is_enabled=False, rollout_percentage=100,
        )
        _service.set_target(
            project_key=flag.project.key, key=flag.key, user=user,
            user_key="alice", variation_id=on.id,
        )
        assert _evaluate(flag, environment, "alice").result is False

    def test_target_beats_a_matching_rule(self, user, eval_setup):
        from apps.rules.models import Rule
        flag, environment, on, off = eval_setup
        Rule.objects.create(
            flag=flag, attribute="user_id", operator="eq",
            value="alice", priority=1, serve_variation=on,
        )
        _service.set_target(
            project_key=flag.project.key, key=flag.key, user=user,
            user_key="alice", variation_id=off.id,
        )
        # Rule says on, target says off — individual targeting wins.
        assert _evaluate(flag, environment, "alice").result is False

    def test_setting_a_target_invalidates_the_cache(self, user, eval_setup):
        flag, environment, on, _off = eval_setup
        # Prime the cache with the pre-target answer.
        assert _evaluate(flag, environment, "alice").result is False
        _service.set_target(
            project_key=flag.project.key, key=flag.key, user=user,
            user_key="alice", variation_id=on.id,
        )
        assert _evaluate(flag, environment, "alice").result is True

    def test_removing_a_target_invalidates_the_cache(self, user, eval_setup):
        flag, environment, on, _off = eval_setup
        _service.set_target(
            project_key=flag.project.key, key=flag.key, user=user,
            user_key="alice", variation_id=on.id,
        )
        assert _evaluate(flag, environment, "alice").result is True
        _service.remove_target(
            project_key=flag.project.key, key=flag.key, user=user, user_key="alice"
        )
        assert _evaluate(flag, environment, "alice").result is False


# ---------------------------------------------------------------------------
# API tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestTargetAPI:
    def test_put_creates_and_returns_201(self, base, auth_client, flag, variations):
        on, _ = variations
        resp = auth_client.put(
            f"{base}/{flag.key}/targets/",
            {"user_key": "alice", "variation": on.id},
            format="json",
        )
        assert resp.status_code == 201
        assert resp.json()["variation_name"] == "on"

    def test_put_on_existing_target_returns_200(self, base, auth_client, flag, variations):
        on, off = variations
        auth_client.put(
            f"{base}/{flag.key}/targets/",
            {"user_key": "alice", "variation": on.id},
            format="json",
        )
        resp = auth_client.put(
            f"{base}/{flag.key}/targets/",
            {"user_key": "alice", "variation": off.id},
            format="json",
        )
        assert resp.status_code == 200
        assert resp.json()["variation_name"] == "off"

    def test_list_returns_targets(self, base, auth_client, flag, variations):
        on, _ = variations
        FlagTarget.objects.create(flag=flag, user_key="alice", variation=on)
        FlagTarget.objects.create(flag=flag, user_key="bob", variation=on)
        resp = auth_client.get(f"{base}/{flag.key}/targets/")
        assert resp.status_code == 200
        assert {row["user_key"] for row in resp.json()} == {"alice", "bob"}

    def test_delete_returns_204(self, base, auth_client, flag, variations):
        on, _ = variations
        FlagTarget.objects.create(flag=flag, user_key="alice", variation=on)
        resp = auth_client.delete(f"{base}/{flag.key}/targets/alice/")
        assert resp.status_code == 204
        assert not FlagTarget.objects.filter(flag=flag, user_key="alice").exists()

    def test_delete_unknown_target_returns_404(self, base, auth_client, flag, variations):
        resp = auth_client.delete(f"{base}/{flag.key}/targets/nobody/")
        assert resp.status_code == 404

    def test_variation_from_another_flag_returns_404(self, base, auth_client, flag):
        foreign = VariationFactory(
            flag=FeatureFlagFactory(project=flag.project),
            name="x", value_type="boolean", value=True,
        )
        resp = auth_client.put(
            f"{base}/{flag.key}/targets/",
            {"user_key": "alice", "variation": foreign.id},
            format="json",
        )
        assert resp.status_code == 404

    def test_another_users_flag_returns_404(self, base, auth_client):
        other_flag = FeatureFlagFactory()
        resp = auth_client.get(f"{base}/{other_flag.key}/targets/")
        assert resp.status_code == 404

    def test_missing_user_key_returns_400(self, base, auth_client, flag, variations):
        on, _ = variations
        resp = auth_client.put(
            f"{base}/{flag.key}/targets/", {"variation": on.id}, format="json"
        )
        assert resp.status_code == 400
