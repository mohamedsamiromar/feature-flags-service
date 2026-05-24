"""
F-06: Archive / unarchive API endpoints — end-to-end tests.

Every test hits the real HTTP stack (DRF router → view → service → DB).
Celery tasks are mocked so no broker is required.
"""

import pytest
from unittest.mock import patch

from apps.audit.models import AuditLog
from apps.flags.models import FeatureFlag

from conftest import FeatureFlagFactory, UserFactory

BASE = "/api/v1/flags"


@pytest.mark.django_db
class TestArchiveEndpoint:
    def test_archive_returns_200(self, auth_client, flag):
        resp = auth_client.post(f"{BASE}/{flag.key}/archive/")
        assert resp.status_code == 200

    def test_archive_sets_is_archived_in_response(self, auth_client, flag):
        resp = auth_client.post(f"{BASE}/{flag.key}/archive/")
        assert resp.json()["is_archived"] is True

    def test_archive_persists_to_db(self, auth_client, flag):
        auth_client.post(f"{BASE}/{flag.key}/archive/")
        flag.refresh_from_db()
        assert flag.is_archived is True

    def test_archive_already_archived_returns_409(self, auth_client, user):
        flag = FeatureFlagFactory(owner=user, is_archived=True)
        resp = auth_client.post(f"{BASE}/{flag.key}/archive/")
        assert resp.status_code == 409

    def test_archive_nonexistent_flag_returns_404(self, auth_client):
        resp = auth_client.post(f"{BASE}/does-not-exist/archive/")
        assert resp.status_code == 404

    def test_archive_another_users_flag_returns_404(self, auth_client):
        other_flag = FeatureFlagFactory()  # different owner
        resp = auth_client.post(f"{BASE}/{other_flag.key}/archive/")
        assert resp.status_code == 404

    def test_archive_writes_audit_log(self, auth_client, flag):
        auth_client.post(f"{BASE}/{flag.key}/archive/")
        assert AuditLog.objects.filter(
            entity_id=str(flag.pk), action="archive"
        ).exists()

    def test_unauthenticated_archive_returns_401(self, api_client, flag):
        resp = api_client.post(f"{BASE}/{flag.key}/archive/")
        assert resp.status_code == 401


@pytest.mark.django_db
class TestUnarchiveEndpoint:
    def test_unarchive_returns_200(self, auth_client, user):
        flag = FeatureFlagFactory(owner=user, is_archived=True)
        resp = auth_client.post(f"{BASE}/{flag.key}/unarchive/")
        assert resp.status_code == 200

    def test_unarchive_sets_is_archived_false_in_response(self, auth_client, user):
        flag = FeatureFlagFactory(owner=user, is_archived=True)
        resp = auth_client.post(f"{BASE}/{flag.key}/unarchive/")
        assert resp.json()["is_archived"] is False

    def test_unarchive_persists_to_db(self, auth_client, user):
        flag = FeatureFlagFactory(owner=user, is_archived=True)
        auth_client.post(f"{BASE}/{flag.key}/unarchive/")
        flag.refresh_from_db()
        assert flag.is_archived is False

    def test_unarchive_active_flag_returns_409(self, auth_client, flag):
        resp = auth_client.post(f"{BASE}/{flag.key}/unarchive/")
        assert resp.status_code == 409

    def test_unarchive_writes_audit_log(self, auth_client, user):
        flag = FeatureFlagFactory(owner=user, is_archived=True)
        auth_client.post(f"{BASE}/{flag.key}/unarchive/")
        assert AuditLog.objects.filter(
            entity_id=str(flag.pk), action="unarchive"
        ).exists()


@pytest.mark.django_db
class TestListWithArchiveFilter:
    def test_list_excludes_archived_by_default(self, auth_client, user):
        FeatureFlagFactory(owner=user, is_archived=False)
        FeatureFlagFactory(owner=user, is_archived=True)
        resp = auth_client.get(f"{BASE}/")
        keys = [f["key"] for f in resp.json()["results"]]
        assert all(
            not FeatureFlag.objects.get(key=k).is_archived
            for k in keys
        )
        assert resp.json()["count"] == 1

    def test_list_includes_archived_with_query_param(self, auth_client, user):
        FeatureFlagFactory(owner=user, is_archived=False)
        FeatureFlagFactory(owner=user, is_archived=True)
        resp = auth_client.get(f"{BASE}/?include_archived=true")
        assert resp.json()["count"] == 2

    def test_is_archived_field_present_in_list_response(self, auth_client, flag):
        resp = auth_client.get(f"{BASE}/")
        result = resp.json()["results"][0]
        assert "is_archived" in result


@pytest.mark.django_db
class TestPatchArchivedFlag:
    def test_patch_archived_flag_returns_409(self, auth_client, user):
        flag = FeatureFlagFactory(owner=user, is_archived=True)
        resp = auth_client.patch(
            f"{BASE}/{flag.key}/",
            {"name": "New Name"},
            format="json",
        )
        assert resp.status_code == 409

    def test_patch_active_flag_succeeds(self, auth_client, flag):
        resp = auth_client.patch(
            f"{BASE}/{flag.key}/",
            {"name": "Updated"},
            format="json",
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated"

    def test_unarchive_then_patch_succeeds(self, auth_client, user):
        flag = FeatureFlagFactory(owner=user, is_archived=True)
        auth_client.post(f"{BASE}/{flag.key}/unarchive/")
        resp = auth_client.patch(
            f"{BASE}/{flag.key}/",
            {"name": "After Unarchive"},
            format="json",
        )
        assert resp.status_code == 200


@pytest.mark.django_db
class TestArchivedFlagEvaluation:
    def test_archived_flag_returns_404_on_evaluate(self, user, environment):
        """Archived flags must not be evaluated — evaluating them returns 404."""
        from conftest import EnvironmentFlagFactory, FeatureFlagFactory, SDKKeyFactory
        from rest_framework.test import APIClient

        flag = FeatureFlagFactory(owner=user, is_archived=True)
        EnvironmentFlagFactory(feature_flag=flag, environment=environment)
        sdk_key = SDKKeyFactory(environment=environment)

        client = APIClient()
        client.credentials(HTTP_X_SDK_KEY=sdk_key._full_key)

        with patch("apps.sdk.views.log_evaluation.delay"):
            resp = client.post(
                "/api/v1/sdk/evaluate/",
                {"flag_key": flag.key, "user_context": {"user_id": "u1"}},
                format="json",
            )
        assert resp.status_code == 404
