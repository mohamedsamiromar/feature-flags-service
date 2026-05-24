"""
F-06: FlagService.archive_flag / unarchive_flag — unit tests.
Cache and audit side-effects are verified alongside state changes.
"""

import pytest
from django.core.exceptions import PermissionDenied
from unittest.mock import patch, call

from apps.audit.models import AuditLog
from apps.flags.models import FeatureFlag
from apps.flags.services import FlagService

from conftest import FeatureFlagFactory, UserFactory

service = FlagService()


@pytest.mark.django_db
class TestArchiveFlag:
    def test_sets_is_archived_true(self, flag, user):
        service.archive_flag(flag, user)
        flag.refresh_from_db()
        assert flag.is_archived is True

    def test_writes_archive_audit_log(self, flag, user):
        service.archive_flag(flag, user)
        log = AuditLog.objects.get(entity_id=str(flag.pk), action="archive")
        assert log.old_value["is_archived"] is False
        assert log.new_value["is_archived"] is True

    def test_invalidates_cache(self, flag, user):
        with patch("apps.flags.services.cache") as mock_cache:
            service.archive_flag(flag, user)
        mock_cache.delete.assert_called_once_with(f"flags:{user.id}:{flag.key}")

    def test_raises_permission_denied_for_non_owner(self, flag, other_user):
        with pytest.raises(PermissionDenied):
            service.archive_flag(flag, other_user)

    def test_returns_the_flag_instance(self, flag, user):
        result = service.archive_flag(flag, user)
        assert result.pk == flag.pk
        assert result.is_archived is True


@pytest.mark.django_db
class TestUnarchiveFlag:
    def test_sets_is_archived_false(self, user):
        flag = FeatureFlagFactory(owner=user, is_archived=True)
        service.unarchive_flag(flag, user)
        flag.refresh_from_db()
        assert flag.is_archived is False

    def test_writes_unarchive_audit_log(self, user):
        flag = FeatureFlagFactory(owner=user, is_archived=True)
        service.unarchive_flag(flag, user)
        log = AuditLog.objects.get(entity_id=str(flag.pk), action="unarchive")
        assert log.old_value["is_archived"] is True
        assert log.new_value["is_archived"] is False

    def test_invalidates_cache(self, user):
        flag = FeatureFlagFactory(owner=user, is_archived=True)
        with patch("apps.flags.services.cache") as mock_cache:
            service.unarchive_flag(flag, user)
        mock_cache.delete.assert_called_once_with(f"flags:{user.id}:{flag.key}")

    def test_raises_permission_denied_for_non_owner(self, other_user):
        flag = FeatureFlagFactory(is_archived=True)
        with pytest.raises(PermissionDenied):
            service.unarchive_flag(flag, other_user)

    def test_returns_the_flag_instance(self, user):
        flag = FeatureFlagFactory(owner=user, is_archived=True)
        result = service.unarchive_flag(flag, user)
        assert result.pk == flag.pk
        assert result.is_archived is False


@pytest.mark.django_db
class TestUpdateFlagBlockedWhenArchived:
    def test_update_flag_still_works_on_active_flag(self, flag, user):
        result = service.update_flag(flag, user, name="Updated Name")
        assert result.name == "Updated Name"

    def test_archive_then_update_via_service_raises(self, flag, user):
        """The archive guard lives in the service layer, not only in the view."""
        from apps.core.exceptions import FlagArchivedError
        service.archive_flag(flag, user)
        flag.refresh_from_db()
        with pytest.raises(FlagArchivedError):
            service.update_flag(flag, user, name="Changed")
