"""
F-06: FeatureFlag model — archive field contract.
"""

import pytest

from conftest import FeatureFlagFactory


@pytest.mark.django_db
class TestFeatureFlagArchiveField:
    def test_is_archived_defaults_false(self, user):
        flag = FeatureFlagFactory(owner=user)
        assert flag.is_archived is False

    def test_is_archived_can_be_set_true(self, user):
        flag = FeatureFlagFactory(owner=user, is_archived=True)
        assert flag.is_archived is True

    def test_is_archived_persists_to_db(self, user):
        from apps.flags.models import FeatureFlag
        flag = FeatureFlagFactory(owner=user)
        flag.is_archived = True
        flag.save(update_fields=["is_archived", "updated_at"])
        refreshed = FeatureFlag.objects.get(pk=flag.pk)
        assert refreshed.is_archived is True

    def test_archived_flag_excluded_from_default_queryset_in_filter(self, user):
        from apps.flags.models import FeatureFlag
        FeatureFlagFactory(owner=user, is_archived=True)
        FeatureFlagFactory(owner=user, is_archived=False)
        active = FeatureFlag.objects.filter(owner=user, is_archived=False)
        archived = FeatureFlag.objects.filter(owner=user, is_archived=True)
        assert active.count() == 1
        assert archived.count() == 1
