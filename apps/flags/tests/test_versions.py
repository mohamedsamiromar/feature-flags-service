"""
Flag version history + rollback — service and API tests.

Versions are recorded on create/update and appended (never rewritten) on
rollback. Fixture-built flags (`FeatureFlagFactory`) bypass the service, so
tests that need history create their flags through `FlagService`.
"""

import pytest
from unittest.mock import patch

from apps.audit.models import AuditLog
from apps.core.errors import APIError
from apps.flags.models import FeatureFlag, FlagVersion, Variation
from apps.flags.services import FlagService

from conftest import FeatureFlagFactory, VariationFactory, personal_project_for

service = FlagService()


def _make_flag(user, **kwargs):
    project = personal_project_for(user)
    kwargs.setdefault("name", "Dark Mode")
    kwargs.setdefault("key", "dark-mode")
    return service.create_flag(project_key=project.key, user=user, **kwargs)


@pytest.mark.django_db
class TestVersionRecording:
    def test_create_records_version_1(self, user):
        flag = _make_flag(user)
        versions = list(flag.versions.all())
        assert len(versions) == 1
        assert versions[0].version_no == 1
        assert versions[0].change_action == FlagVersion.ChangeAction.CREATE
        assert versions[0].changed_by_id == user.id

    def test_snapshot_captures_config(self, user):
        flag = _make_flag(user, rollout_percentage=25)
        snap = flag.versions.get(version_no=1).snapshot
        assert snap["rollout_percentage"] == 25
        assert snap["name"] == "Dark Mode"

    def test_update_appends_incrementing_versions(self, user):
        flag = _make_flag(user)
        service.update_flag(flag.project.key, flag.key, user, rollout_percentage=50)
        service.update_flag(flag.project.key, flag.key, user, rollout_percentage=75)
        nums = list(flag.versions.values_list("version_no", flat=True).order_by("version_no"))
        assert nums == [1, 2, 3]
        assert flag.versions.get(version_no=3).snapshot["rollout_percentage"] == 75


@pytest.mark.django_db
class TestRollbackService:
    def test_restores_prior_config(self, user):
        flag = _make_flag(user, rollout_percentage=10)
        service.update_flag(flag.project.key, flag.key, user, rollout_percentage=90)
        service.rollback(flag.project.key, flag.key, user, version_no=1)
        flag.refresh_from_db()
        assert flag.rollout_percentage == 10

    def test_rollback_appends_new_version(self, user):
        flag = _make_flag(user, rollout_percentage=10)
        service.update_flag(flag.project.key, flag.key, user, rollout_percentage=90)
        service.rollback(flag.project.key, flag.key, user, version_no=1)
        latest = flag.versions.first()  # newest-first ordering
        assert latest.version_no == 3
        assert latest.change_action == FlagVersion.ChangeAction.ROLLBACK
        assert latest.source_version_no == 1

    def test_rollback_writes_audit_log(self, user):
        flag = _make_flag(user, rollout_percentage=10)
        service.update_flag(flag.project.key, flag.key, user, rollout_percentage=90)
        service.rollback(flag.project.key, flag.key, user, version_no=1)
        log = AuditLog.objects.get(entity_id=str(flag.pk), action="rollback")
        assert log.old_value["rollout_percentage"] == 90
        assert log.new_value["rollout_percentage"] == 10

    def test_unknown_version_raises(self, user):
        flag = _make_flag(user)
        with pytest.raises(APIError):
            service.rollback(flag.project.key, flag.key, user, version_no=999)

    def test_archived_flag_raises(self, user):
        flag = _make_flag(user)
        service.archive_flag(flag.project.key, flag.key, user)
        flag.refresh_from_db()
        with pytest.raises(APIError):
            service.rollback(flag.project.key, flag.key, user, version_no=1)

    def test_non_owner_raises(self, user, other_user):
        flag = _make_flag(user)
        with pytest.raises(APIError):
            service.rollback(flag.project.key, flag.key, other_user, version_no=1)

    def test_invalidates_cache(self, user, environment):
        from conftest import EnvironmentFlagFactory
        flag = _make_flag(user)
        service.update_flag(flag.project.key, flag.key, user, rollout_percentage=50)
        EnvironmentFlagFactory(feature_flag=flag, environment=environment)
        with patch("apps.evaluation.services.cache") as mock_cache:
            service.rollback(flag.project.key, flag.key, user, version_no=1)
        mock_cache.delete.assert_called_once_with(
            f"flags:{flag.project_id}:{environment.id}:{flag.key}"
        )

    def test_dangling_variation_ref_is_dropped(self, user):
        """A snapshot pointing at a since-deleted variation restores to None."""
        flag = _make_flag(user, flag_type=FeatureFlag.FlagType.MULTIVARIATE)
        var = VariationFactory(flag=flag, name="blue")
        service.update_flag(flag.project.key, flag.key, user, fallthrough_variation=var)  # v2 snapshots var.id
        service.update_flag(flag.project.key, flag.key, user, fallthrough_variation=None)  # v3
        var.delete()
        service.rollback(flag.project.key, flag.key, user, version_no=2)
        flag.refresh_from_db()
        assert flag.fallthrough_variation_id is None


@pytest.mark.django_db
class TestVersionAPI:
    def test_list_returns_versions_newest_first(self, base, auth_client, user):
        flag = _make_flag(user)
        service.update_flag(flag.project.key, flag.key, user, rollout_percentage=50)
        resp = auth_client.get(f"{base}/{flag.key}/versions/")
        assert resp.status_code == 200
        body = resp.json()
        assert [v["version_no"] for v in body] == [2, 1]

    def test_version_detail(self, base, auth_client, user):
        flag = _make_flag(user, rollout_percentage=33)
        resp = auth_client.get(f"{base}/{flag.key}/versions/1/")
        assert resp.status_code == 200
        assert resp.json()["snapshot"]["rollout_percentage"] == 33

    def test_rollback_returns_200_and_restores(self, base, auth_client, user):
        flag = _make_flag(user, rollout_percentage=10)
        service.update_flag(flag.project.key, flag.key, user, rollout_percentage=90)
        resp = auth_client.post(f"{base}/{flag.key}/versions/1/rollback/")
        assert resp.status_code == 200
        assert resp.json()["rollout_percentage"] == 10

    def test_rollback_unknown_version_returns_404(self, base, auth_client, user):
        flag = _make_flag(user)
        resp = auth_client.post(f"{base}/{flag.key}/versions/999/rollback/")
        assert resp.status_code == 404

    def test_rollback_unknown_flag_returns_404(self, base, auth_client):
        resp = auth_client.post(f"{base}/nope/versions/1/rollback/")
        assert resp.status_code == 404

    def test_rollback_another_users_flag_returns_404(self, base, auth_client):
        other_flag = FeatureFlagFactory()  # different owner
        resp = auth_client.post(f"{base}/{other_flag.key}/versions/1/rollback/")
        assert resp.status_code == 404

    def test_rollback_archived_flag_returns_409(self, base, auth_client, user):
        flag = _make_flag(user)
        service.archive_flag(flag.project.key, flag.key, user)
        resp = auth_client.post(f"{base}/{flag.key}/versions/1/rollback/")
        assert resp.status_code == 409
