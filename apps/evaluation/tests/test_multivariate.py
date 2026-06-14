"""
Multivariate evaluation tests.

Verifies that EvaluationResult carries the correct value + result_type
for both legacy boolean flags and new multivariate flags.
"""

import pytest
from conftest import (
    EnvironmentFactory, EnvironmentFlagFactory,
    FeatureFlagFactory, SDKKeyFactory, VariationFactory,
)
from apps.flags.models import FeatureFlag, Variation
from apps.flags.services import FlagService
from apps.evaluation.services import FlagEvaluationService
from unittest.mock import patch

BASE = "/api/v1/sdk/evaluate/"
_eval_service = FlagEvaluationService()
_flag_service = FlagService()


# ---------------------------------------------------------------------------
# Service-level evaluation tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBooleanFlagEvaluation:
    def test_enabled_boolean_flag_returns_true_bool(self, user, environment):
        flag = _flag_service.create_flag(user=user, name="F", key="ev-bool-on")
        EnvironmentFlagFactory(
            feature_flag=flag, environment=environment,
            is_enabled=True, rollout_percentage=100,
        )
        result = _eval_service.evaluate(
            flag_key="ev-bool-on",
            owner_id=user.id,
            user_context={"user_id": "u1"},
            env_id=environment.id,
        )
        assert result.result is True
        assert result.result_type == "boolean"

    def test_disabled_boolean_flag_returns_false_bool(self, user, environment):
        flag = _flag_service.create_flag(user=user, name="F", key="ev-bool-off")
        EnvironmentFlagFactory(
            feature_flag=flag, environment=environment,
            is_enabled=False,
        )
        result = _eval_service.evaluate(
            flag_key="ev-bool-off",
            owner_id=user.id,
            user_context={"user_id": "u1"},
            env_id=environment.id,
        )
        assert result.result is False
        assert result.result_type == "boolean"


@pytest.mark.django_db
class TestMultivariateFlagEvaluation:
    def _make_mv_flag(self, user, environment, key):
        flag = FeatureFlagFactory(
            owner=user, key=key,
            flag_type=FeatureFlag.FlagType.MULTIVARIATE,
        )
        red = VariationFactory(flag=flag, name="red", value_type="string", value="red")
        blue = VariationFactory(flag=flag, name="blue", value_type="string", value="blue")
        flag.off_variation = blue
        flag.fallthrough_variation = red
        flag.save(update_fields=["off_variation", "fallthrough_variation"])
        EnvironmentFlagFactory(
            feature_flag=flag, environment=environment,
            is_enabled=True, rollout_percentage=100,
        )
        return flag

    def test_mv_flag_returns_variation_value(self, user, environment):
        self._make_mv_flag(user, environment, "mv-1")
        result = _eval_service.evaluate(
            flag_key="mv-1",
            owner_id=user.id,
            user_context={"user_id": "u1"},
            env_id=environment.id,
        )
        assert result.result == "red"
        assert result.result_type == "string"

    def test_mv_disabled_returns_off_variation(self, user, environment):
        flag = FeatureFlagFactory(
            owner=user, key="mv-off",
            flag_type=FeatureFlag.FlagType.MULTIVARIATE,
        )
        red = VariationFactory(flag=flag, name="red", value_type="string", value="red")
        blue = VariationFactory(flag=flag, name="blue", value_type="string", value="blue")
        flag.off_variation = blue
        flag.fallthrough_variation = red
        flag.save(update_fields=["off_variation", "fallthrough_variation"])
        EnvironmentFlagFactory(
            feature_flag=flag, environment=environment,
            is_enabled=False,
        )
        result = _eval_service.evaluate(
            flag_key="mv-off",
            owner_id=user.id,
            user_context={"user_id": "u1"},
            env_id=environment.id,
        )
        assert result.result == "blue"

    def test_mv_json_variation(self, user, environment):
        flag = FeatureFlagFactory(
            owner=user, key="mv-json",
            flag_type=FeatureFlag.FlagType.MULTIVARIATE,
        )
        payload = {"color": "#ff0000", "weight": 700}
        v = VariationFactory(flag=flag, name="config", value_type="json", value=payload)
        flag.off_variation = v
        flag.fallthrough_variation = v
        flag.save(update_fields=["off_variation", "fallthrough_variation"])
        EnvironmentFlagFactory(
            feature_flag=flag, environment=environment,
            is_enabled=True, rollout_percentage=100,
        )
        result = _eval_service.evaluate(
            flag_key="mv-json",
            owner_id=user.id,
            user_context={"user_id": "u1"},
            env_id=environment.id,
        )
        assert result.result == payload
        assert result.result_type == "json"

    def test_number_variation(self, user, environment):
        flag = FeatureFlagFactory(
            owner=user, key="mv-num",
            flag_type=FeatureFlag.FlagType.MULTIVARIATE,
        )
        v = VariationFactory(flag=flag, name="timeout", value_type="number", value=5000)
        flag.off_variation = v
        flag.fallthrough_variation = v
        flag.save(update_fields=["off_variation", "fallthrough_variation"])
        EnvironmentFlagFactory(
            feature_flag=flag, environment=environment,
            is_enabled=True, rollout_percentage=100,
        )
        result = _eval_service.evaluate(
            flag_key="mv-num",
            owner_id=user.id,
            user_context={"user_id": "u1"},
            env_id=environment.id,
        )
        assert result.result == 5000
        assert result.result_type == "number"


# ---------------------------------------------------------------------------
# SDK endpoint tests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestSDKEvaluateMultivariate:
    def test_response_includes_result_type_for_boolean_flag(self, user, environment):
        flag = _flag_service.create_flag(user=user, name="F", key="sdk-bool")
        EnvironmentFlagFactory(
            feature_flag=flag, environment=environment,
            is_enabled=True, rollout_percentage=100,
        )
        sdk_key = SDKKeyFactory(environment=environment)
        from rest_framework.test import APIClient
        client = APIClient()
        client.credentials(HTTP_X_SDK_KEY=sdk_key._full_key)
        with patch("apps.sdk.views.log_evaluation.delay"):
            resp = client.post(
                BASE,
                {"flag_key": "sdk-bool", "user_context": {"user_id": "u1"}},
                format="json",
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["result"] is True
        assert data["result_type"] == "boolean"

    def test_response_includes_result_type_for_string_variation(self, user, environment):
        flag = FeatureFlagFactory(
            owner=user, key="sdk-mv",
            flag_type=FeatureFlag.FlagType.MULTIVARIATE,
        )
        v = VariationFactory(flag=flag, name="red", value_type="string", value="red")
        flag.off_variation = v
        flag.fallthrough_variation = v
        flag.save(update_fields=["off_variation", "fallthrough_variation"])
        EnvironmentFlagFactory(
            feature_flag=flag, environment=environment,
            is_enabled=True, rollout_percentage=100,
        )
        sdk_key = SDKKeyFactory(environment=environment)
        from rest_framework.test import APIClient
        client = APIClient()
        client.credentials(HTTP_X_SDK_KEY=sdk_key._full_key)
        with patch("apps.sdk.views.log_evaluation.delay"):
            resp = client.post(
                BASE,
                {"flag_key": "sdk-mv", "user_context": {"user_id": "u1"}},
                format="json",
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["result"] == "red"
        assert data["result_type"] == "string"
