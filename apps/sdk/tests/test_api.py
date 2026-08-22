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


@pytest.mark.django_db
class TestSDKEvaluateIndividualTargeting:
    """Individual targets must reach the SDK through the real HTTP path,
    keyed off the `user_id` the SDK sends in its evaluation context."""

    @pytest.fixture
    def targeted(self, flag, environment, sdk_key, user):
        from apps.flags.services import FlagService
        from conftest import VariationFactory

        on = VariationFactory(flag=flag, name="on", value_type="boolean", value=True)
        off = VariationFactory(flag=flag, name="off", value_type="boolean", value=False)
        flag.fallthrough_variation, flag.off_variation = on, off
        flag.save(update_fields=["fallthrough_variation", "off_variation"])

        # On, but nobody is in the rollout — only a target can get through.
        EnvironmentFlagFactory(
            feature_flag=flag, environment=environment,
            is_enabled=True, rollout_percentage=0,
        )
        FlagService().set_target(
            project_key=flag.project.key, key=flag.key, user=user,
            user_key="alice", variation_id=on.id,
        )
        return flag, sdk_key

    def _evaluate(self, api_client, flag, sdk_key, user_id):
        with _patch_celery():
            return api_client.post(
                ENDPOINT,
                {"flag_key": flag.key, "user_context": {"user_id": user_id}},
                format="json",
                HTTP_X_SDK_KEY=sdk_key._full_key,
            )

    def test_targeted_user_receives_true(self, api_client, targeted):
        flag, sdk_key = targeted
        resp = self._evaluate(api_client, flag, sdk_key, "alice")
        assert resp.status_code == 200
        assert resp.json()["result"] is True

    def test_untargeted_user_receives_false(self, api_client, targeted):
        flag, sdk_key = targeted
        resp = self._evaluate(api_client, flag, sdk_key, "bob")
        assert resp.status_code == 200
        assert resp.json()["result"] is False


@pytest.mark.django_db
class TestEvaluationTaskArgsStayJsonSafe:
    """Everything handed to a Celery task must survive JSON serialization.

    `CELERY_TASK_SERIALIZER` is "json", and the cached flag config contains
    Python sets (segment include/exclude lists), which json.dumps cannot
    encode. Today only the evaluated result is passed, so this holds — but
    "send the evaluation data to a task" is exactly the shape of the batching
    work on the roadmap, and the failure would surface in the async path where
    it is easy to miss. This test fails the moment flag config leaks into a
    task argument.
    """

    @pytest.fixture
    def segment_targeted_flag(self, user, project, flag, environment, sdk_key):
        from conftest import VariationFactory
        from apps.rules.models import Operator, Rule
        from apps.segments.services import SegmentService

        segments = SegmentService()
        segment = segments.create_segment(
            project_key=project.key, user=user, key="beta", name="Beta"
        )
        segments.set_target(
            project_key=project.key, key=segment.key, user=user,
            user_key="alice", excluded=False,
        )
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
        return flag, sdk_key

    def test_task_kwargs_are_json_encodable(self, api_client, segment_targeted_flag):
        import json

        flag, sdk_key = segment_targeted_flag
        with _patch_celery() as delay:
            resp = api_client.post(
                ENDPOINT,
                {"flag_key": flag.key, "user_context": {"user_id": "alice"}},
                format="json",
                HTTP_X_SDK_KEY=sdk_key._full_key,
            )
        assert resp.status_code == 200
        assert delay.called

        # Raises TypeError if any argument carries a set (or anything else
        # Celery's json serializer cannot encode).
        json.dumps(delay.call_args.kwargs)
        json.dumps(list(delay.call_args.args))
