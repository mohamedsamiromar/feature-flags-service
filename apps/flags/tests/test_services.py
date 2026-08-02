"""
F-06: FlagService.archive_flag / unarchive_flag — unit tests.
Cache and audit side-effects are verified alongside state changes.

Services resolve the flag through project membership, so a non-member sees a
404-shaped ``APIError`` (not a 403) and callers pass ``project_key`` + ``key``
rather than an instance.
"""

import pytest
from unittest.mock import patch

from apps.audit.models import AuditLog
from apps.core.errors import APIError
from apps.flags.services import FlagService

from conftest import FeatureFlagFactory

service = FlagService()


@pytest.mark.django_db
class TestArchiveFlag:
    def test_sets_is_archived_true(self, flag, user):
        service.archive_flag(flag.project.key, flag.key, user)
        flag.refresh_from_db()
        assert flag.is_archived is True

    def test_writes_archive_audit_log(self, flag, user):
        service.archive_flag(flag.project.key, flag.key, user)
        log = AuditLog.objects.get(entity_id=str(flag.pk), action="archive")
        assert log.old_value["is_archived"] is False
        assert log.new_value["is_archived"] is True

    def test_invalidates_cache(self, flag, environment, user):
        from conftest import EnvironmentFlagFactory
        EnvironmentFlagFactory(feature_flag=flag, environment=environment)
        with patch("apps.evaluation.services.cache") as mock_cache:
            service.archive_flag(flag.project.key, flag.key, user)
        mock_cache.delete.assert_called_once_with(
            f"flags:{flag.project_id}:{environment.id}:{flag.key}"
        )

    def test_raises_not_found_for_non_member(self, flag, other_user):
        with pytest.raises(APIError):
            service.archive_flag(flag.project.key, flag.key, other_user)

    def test_returns_the_flag_instance(self, flag, user):
        result = service.archive_flag(flag.project.key, flag.key, user)
        assert result.pk == flag.pk
        assert result.is_archived is True


@pytest.mark.django_db
class TestUnarchiveFlag:
    def test_sets_is_archived_false(self, user, project):
        flag = FeatureFlagFactory(project=project, is_archived=True)
        service.unarchive_flag(project.key, flag.key, user)
        flag.refresh_from_db()
        assert flag.is_archived is False

    def test_writes_unarchive_audit_log(self, user, project):
        flag = FeatureFlagFactory(project=project, is_archived=True)
        service.unarchive_flag(project.key, flag.key, user)
        log = AuditLog.objects.get(entity_id=str(flag.pk), action="unarchive")
        assert log.old_value["is_archived"] is True
        assert log.new_value["is_archived"] is False

    def test_invalidates_cache(self, environment, user, project):
        from conftest import EnvironmentFlagFactory
        flag = FeatureFlagFactory(project=project, is_archived=True)
        EnvironmentFlagFactory(feature_flag=flag, environment=environment)
        with patch("apps.evaluation.services.cache") as mock_cache:
            service.unarchive_flag(project.key, flag.key, user)
        mock_cache.delete.assert_called_once_with(
            f"flags:{flag.project_id}:{environment.id}:{flag.key}"
        )

    def test_raises_not_found_for_non_member(self, other_user):
        flag = FeatureFlagFactory(is_archived=True)
        with pytest.raises(APIError):
            service.unarchive_flag(flag.project.key, flag.key, other_user)

    def test_returns_the_flag_instance(self, user, project):
        flag = FeatureFlagFactory(project=project, is_archived=True)
        result = service.unarchive_flag(project.key, flag.key, user)
        assert result.pk == flag.pk
        assert result.is_archived is False


@pytest.mark.django_db
class TestUpdateFlagBlockedWhenArchived:
    def test_update_flag_still_works_on_active_flag(self, flag, user):
        result = service.update_flag(flag.project.key, flag.key, user, name="Updated Name")
        assert result.name == "Updated Name"

    def test_archive_then_update_via_service_raises(self, flag, user):
        """The archive guard lives in the service layer, not only in the view."""
        service.archive_flag(flag.project.key, flag.key, user)
        flag.refresh_from_db()
        with pytest.raises(APIError):
            service.update_flag(flag.project.key, flag.key, user, name="Changed")
