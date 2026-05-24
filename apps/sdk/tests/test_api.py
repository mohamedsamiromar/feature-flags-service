"""
F-03: SDK evaluate endpoint — end-to-end tests.

POST /api/v1/sdk/evaluate/ requires X-SDK-Key header.
JWT tokens must NOT work here (SDK-key-only endpoint).
"""

import pytest
from unittest.mock import patch

from conftest import (
    EnvironmentFlagFactory,
    FeatureFlagFactory,
    SDKKeyFactory,
    UserFactory,
)

ENDPOINT = "/api/v1/sdk/evaluate/"


def _patch_celery():
    """Prevent real Celery task dispatch during evaluation."""
    return patch("apps.sdk.views.log_evaluation.delay")


@pytest.mark.django_db
class TestSDKEvaluateAuthentication:
    def test_valid_server_key_returns_200(self, api_client, environment_flag, sdk_key):
        with _patch_celery():
            resp = api_client.post(
                ENDPOINT,
                {"flag_key": environment_flag.feature_flag.key, "user_context": {"user_id": "u1"}},
                format="json",
                HTTP_X_SDK_KEY=sdk_key._full_key,
            )
        assert resp.status_code == 200

    def test_valid_client_key_also_accepted(self, api_client, environment, environment_flag):
        client_key = SDKKeyFactory(
            environment=environment,
            key_type="client",
        )
        with _patch_celery():
            resp = api_client.post(
                ENDPOINT,
                {"flag_key": environment_flag.feature_flag.key, "user_context": {"user_id": "u1"}},
                format="json",
                HTTP_X_SDK_KEY=client_key._full_key,
            )
        assert resp.status_code == 200

    def test_invalid_sdk_key_returns_401(self, api_client):
        resp = api_client.post(
            ENDPOINT,
            {"flag_key": "any-flag", "user_context": {}},
            format="json",
            HTTP_X_SDK_KEY="sdk_srv_totally_wrong",
        )
        assert resp.status_code == 401

    def test_missing_sdk_key_returns_401(self, api_client):
        resp = api_client.post(
            ENDPOINT,
            {"flag_key": "any-flag", "user_context": {}},
            format="json",
        )
        assert resp.status_code == 401

    def test_jwt_token_not_accepted(self, api_client, user, environment_flag):
        """JWT token in Authorization header must be rejected (SDK-key-only endpoint)."""
        from rest_framework_simplejwt.tokens import RefreshToken
        access_token = str(RefreshToken.for_user(user).access_token)
        with _patch_celery():
            resp = api_client.post(
                ENDPOINT,
                {"flag_key": environment_flag.feature_flag.key, "user_context": {}},
                format="json",
                HTTP_AUTHORIZATION=f"Bearer {access_token}",
            )
        assert resp.status_code == 401

    def test_revoked_key_returns_401(self, api_client, sdk_key, environment_flag):
        sdk_key.is_active = False
        sdk_key.save()
        resp = api_client.post(
            ENDPOINT,
            {"flag_key": environment_flag.feature_flag.key, "user_context": {}},
            format="json",
            HTTP_X_SDK_KEY=sdk_key._full_key,
        )
        assert resp.status_code == 401


@pytest.mark.django_db
class TestSDKEvaluateResponse:
    def test_response_contains_flag_key(self, api_client, environment_flag, sdk_key):
        with _patch_celery():
            resp = api_client.post(
                ENDPOINT,
                {"flag_key": environment_flag.feature_flag.key, "user_context": {"user_id": "u1"}},
                format="json",
                HTTP_X_SDK_KEY=sdk_key._full_key,
            )
        assert resp.json()["flag_key"] == environment_flag.feature_flag.key

    def test_response_contains_result_bool(self, api_client, environment_flag, sdk_key):
        with _patch_celery():
            resp = api_client.post(
                ENDPOINT,
                {"flag_key": environment_flag.feature_flag.key, "user_context": {"user_id": "u1"}},
                format="json",
                HTTP_X_SDK_KEY=sdk_key._full_key,
            )
        assert isinstance(resp.json()["result"], bool)

    def test_response_contains_environment_name(self, api_client, environment_flag, sdk_key):
        with _patch_celery():
            resp = api_client.post(
                ENDPOINT,
                {"flag_key": environment_flag.feature_flag.key, "user_context": {"user_id": "u1"}},
                format="json",
                HTTP_X_SDK_KEY=sdk_key._full_key,
            )
        assert resp.json()["environment"] == sdk_key.environment.name

    def test_env_id_derived_from_key_not_request_body(self, api_client, environment_flag, sdk_key):
        """env_id in body is ignored — it comes from the SDK key itself."""
        with _patch_celery():
            # Pass a wrong env_id in the body — should not matter
            resp = api_client.post(
                ENDPOINT,
                {
                    "flag_key": environment_flag.feature_flag.key,
                    "user_context": {"user_id": "u1"},
                    "env_id": 99999,  # ignored
                },
                format="json",
                HTTP_X_SDK_KEY=sdk_key._full_key,
            )
        assert resp.status_code == 200

    def test_enabled_flag_returns_true(self, api_client, user, environment, sdk_key):
        flag = FeatureFlagFactory(owner=user, is_enabled=True, rollout_percentage=100)
        EnvironmentFlagFactory(feature_flag=flag, environment=environment, is_enabled=True, rollout_percentage=100)
        with _patch_celery():
            resp = api_client.post(
                ENDPOINT,
                {"flag_key": flag.key, "user_context": {"user_id": "u1"}},
                format="json",
                HTTP_X_SDK_KEY=sdk_key._full_key,
            )
        assert resp.json()["result"] is True

    def test_disabled_env_flag_returns_false(self, api_client, user, environment, sdk_key):
        flag = FeatureFlagFactory(owner=user, is_enabled=True)
        EnvironmentFlagFactory(feature_flag=flag, environment=environment, is_enabled=False)
        with _patch_celery():
            resp = api_client.post(
                ENDPOINT,
                {"flag_key": flag.key, "user_context": {"user_id": "u1"}},
                format="json",
                HTTP_X_SDK_KEY=sdk_key._full_key,
            )
        assert resp.json()["result"] is False


@pytest.mark.django_db
class TestSDKEvaluateEdgeCases:
    def test_missing_flag_returns_404(self, api_client, sdk_key):
        with _patch_celery():
            resp = api_client.post(
                ENDPOINT,
                {"flag_key": "non-existent-flag", "user_context": {}},
                format="json",
                HTTP_X_SDK_KEY=sdk_key._full_key,
            )
        assert resp.status_code == 404

    def test_archived_flag_returns_404(self, api_client, user, environment, sdk_key):
        flag = FeatureFlagFactory(owner=user, is_archived=True)
        EnvironmentFlagFactory(feature_flag=flag, environment=environment, is_enabled=True)
        with _patch_celery():
            resp = api_client.post(
                ENDPOINT,
                {"flag_key": flag.key, "user_context": {"user_id": "u1"}},
                format="json",
                HTTP_X_SDK_KEY=sdk_key._full_key,
            )
        assert resp.status_code == 404

    def test_impression_task_dispatched_on_success(self, api_client, environment_flag, sdk_key):
        with patch("apps.sdk.views.log_evaluation.delay") as mock_delay:
            api_client.post(
                ENDPOINT,
                {"flag_key": environment_flag.feature_flag.key, "user_context": {"user_id": "u1"}},
                format="json",
                HTTP_X_SDK_KEY=sdk_key._full_key,
            )
        mock_delay.assert_called_once()

    def test_empty_user_context_accepted(self, api_client, environment_flag, sdk_key):
        """user_context is optional — empty dict is valid."""
        with _patch_celery():
            resp = api_client.post(
                ENDPOINT,
                {"flag_key": environment_flag.feature_flag.key, "user_context": {}},
                format="json",
                HTTP_X_SDK_KEY=sdk_key._full_key,
            )
        assert resp.status_code == 200
