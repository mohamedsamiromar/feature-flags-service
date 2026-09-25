"""
The engine never serves a variation that belongs to a different flag.

The write paths now reject foreign variation references, but rows written
before that fix still exist wherever the API was reachable. These tests plant
such rows directly — the way pre-fix data looks — and pin that no SDK surface
serves the foreign value: a reference the engine cannot trust is dropped, the
same way rollback drops a variation that no longer belongs to the flag.
"""

from unittest.mock import patch

import pytest

from apps.flags.models import FeatureFlag, Variation
from apps.rules.models import Rule
from conftest import (
    EnvironmentFactory,
    EnvironmentFlagFactory,
    FeatureFlagFactory,
    ProjectFactory,
    SDKKeyFactory,
    VariationFactory,
)

SECRET = "tenant-b-secret"


@pytest.fixture
def foreign_variation(db):
    return VariationFactory(
        flag=FeatureFlagFactory(project=ProjectFactory()),
        value_type=Variation.ValueType.STRING,
        value=SECRET,
    )


@pytest.fixture
def attacker(db):
    """A project, environment, and server key belonging to the attacker."""
    project = ProjectFactory()
    environment = EnvironmentFactory(project=project)
    return project, environment, SDKKeyFactory(environment=environment)


def _live_flag(project, environment, is_enabled=True, **fields):
    flag = FeatureFlagFactory(
        project=project, flag_type=FeatureFlag.FlagType.MULTIVARIATE, **fields
    )
    EnvironmentFlagFactory(
        feature_flag=flag, environment=environment, is_enabled=is_enabled
    )
    return flag


def _surfaces(api_client, key, flag_key):
    """Every SDK response that could carry the flag's served value."""
    headers = {"HTTP_X_SDK_KEY": key._full_key}
    body = {"flag_key": flag_key, "user_context": {"user_id": "u1", "plan": "pro"}}
    with patch("apps.sdk.views.log_evaluation.delay"):
        single = api_client.post("/api/v1/sdk/evaluate/", body, format="json", **headers)
    bulk = api_client.post(
        "/api/v1/sdk/flags/evaluate/",
        {"user_context": body["user_context"]},
        format="json",
        **headers,
    )
    config = api_client.get("/api/v1/sdk/flags/config/", **headers)
    for resp in (single, bulk, config):
        assert resp.status_code == 200, resp.content
    return {"single": single.json(), "bulk": bulk.json(), "config": config.json()}


def _assert_secret_absent(surfaces):
    for name, payload in surfaces.items():
        # Search the parsed payload, not the raw bytes: str(resp.content)
        # escapes quotes and once hid a leak from exactly this kind of check.
        assert SECRET not in repr(payload), f"foreign value leaked via {name}"


@pytest.mark.django_db
class TestPreFixRowsDoNotLeak:
    def test_foreign_off_variation(self, api_client, attacker, foreign_variation):
        project, environment, key = attacker
        flag = _live_flag(
            project, environment, is_enabled=False, off_variation=foreign_variation
        )
        _assert_secret_absent(_surfaces(api_client, key, flag.key))

    def test_foreign_fallthrough_variation(self, api_client, attacker, foreign_variation):
        project, environment, key = attacker
        flag = _live_flag(
            project, environment, rollout_percentage=100,
            fallthrough_variation=foreign_variation,
        )
        _assert_secret_absent(_surfaces(api_client, key, flag.key))

    def test_foreign_rule_serve_variation(self, api_client, attacker, foreign_variation):
        project, environment, key = attacker
        flag = _live_flag(project, environment)
        Rule.objects.create(
            flag=flag, attribute="plan", operator="eq", value="pro",
            serve_variation=foreign_variation,
        )
        _assert_secret_absent(_surfaces(api_client, key, flag.key))


@pytest.mark.django_db
class TestOwnVariationsStillServed:
    def test_own_rule_variation_is_served(self, api_client, attacker):
        """Control: the guard must not drop a flag's own variations."""
        project, environment, key = attacker
        flag = _live_flag(project, environment)
        own = VariationFactory(flag=flag, value_type=Variation.ValueType.STRING, value="own")
        Rule.objects.create(
            flag=flag, attribute="plan", operator="eq", value="pro", serve_variation=own
        )
        surfaces = _surfaces(api_client, key, flag.key)
        assert surfaces["single"]["result"] == "own"
        assert surfaces["bulk"]["flags"][flag.key]["result"] == "own"
