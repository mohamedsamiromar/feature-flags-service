"""
A client SDK key only reaches flags marked `client_side_available`.

Client keys (`sdk_cli_`) ship to browsers, so anyone can read one. Before this,
one call to the bootstrap endpoint with such a key listed every flag key and
served value in the environment — unreleased features, internal config,
anything a server-side flag held. Server keys are unaffected.

New flags are hidden from client keys by default; the migration marked flags
that existed before it visible, so live browser SDKs kept working.
"""

from unittest.mock import patch

import pytest

from apps.flags.models import FeatureFlag, FlagPrerequisite
from apps.sdk_keys.models import SDKKey
from conftest import (
    EnvironmentFactory,
    EnvironmentFlagFactory,
    FeatureFlagFactory,
    SDKKeyFactory,
    VariationFactory,
)

EVALUATE = "/api/v1/sdk/evaluate/"
BOOTSTRAP = "/api/v1/sdk/flags/evaluate/"
IMPRESSIONS = "/api/v1/sdk/impressions/"
CONTEXT = {"user_id": "u1"}


@pytest.fixture
def env(project):
    return EnvironmentFactory(project=project)


@pytest.fixture
def client_key(env):
    return SDKKeyFactory(environment=env, key_type=SDKKey.KeyType.CLIENT)


@pytest.fixture
def server_key(env):
    return SDKKeyFactory(environment=env, key_type=SDKKey.KeyType.SERVER)


def _flag(project, env, key, visible):
    flag = FeatureFlagFactory(project=project, key=key, client_side_available=visible)
    EnvironmentFlagFactory(feature_flag=flag, environment=env)
    return flag


def _bootstrap(api_client, sdk_key):
    resp = api_client.post(
        BOOTSTRAP, {"user_context": CONTEXT}, format="json",
        HTTP_X_SDK_KEY=sdk_key._full_key,
    )
    assert resp.status_code == 200, resp.content
    return resp.json()["flags"]


def _evaluate(api_client, sdk_key, flag_key):
    with patch("apps.sdk.views.log_evaluation.delay"):
        return api_client.post(
            EVALUATE, {"flag_key": flag_key, "user_context": CONTEXT}, format="json",
            HTTP_X_SDK_KEY=sdk_key._full_key,
        )


@pytest.mark.django_db
class TestDefault:
    def test_new_flag_is_hidden_from_client_keys(self, auth_client, base):
        resp = auth_client.post(f"{base}/", {"name": "New", "key": "new"}, format="json")
        assert resp.status_code == 201
        assert resp.data["client_side_available"] is False

    def test_can_opt_in_on_create_and_update(self, auth_client, base):
        created = auth_client.post(
            f"{base}/",
            {"name": "New", "key": "new", "client_side_available": True},
            format="json",
        )
        assert created.data["client_side_available"] is True

        updated = auth_client.patch(
            f"{base}/new/", {"client_side_available": False}, format="json"
        )
        assert updated.status_code == 200
        assert FeatureFlag.objects.get(key="new").client_side_available is False


@pytest.mark.django_db
class TestBootstrap:
    def test_client_key_sees_only_visible_flags(
        self, api_client, project, env, client_key
    ):
        _flag(project, env, "public-banner", visible=True)
        _flag(project, env, "secret-launch", visible=False)

        assert set(_bootstrap(api_client, client_key)) == {"public-banner"}

    def test_server_key_sees_everything(self, api_client, project, env, server_key):
        _flag(project, env, "public-banner", visible=True)
        _flag(project, env, "secret-launch", visible=False)

        assert set(_bootstrap(api_client, server_key)) == {"public-banner", "secret-launch"}

    def test_hidden_prerequisite_still_gates_a_visible_flag(
        self, api_client, project, env, client_key
    ):
        """Hiding a flag hides its *result*, not its effect: a visible flag
        gated behind it must still resolve through it, not fail open or closed."""
        gate = _flag(project, env, "gate", visible=False)
        on = VariationFactory(flag=gate, value=True)
        gate.fallthrough_variation = on
        gate.off_variation = VariationFactory(flag=gate, value=False)
        gate.rollout_percentage = 100
        gate.save()
        gated = _flag(project, env, "gated", visible=True)
        gated.rollout_percentage = 100
        gated.save()
        FlagPrerequisite.objects.create(
            flag=gated, prerequisite_flag=gate, required_variation=on
        )

        flags = _bootstrap(api_client, client_key)

        assert "gate" not in flags
        # The gate is met, so the gated flag is on — and the server key agrees.
        assert flags["gated"]["result"] is True
        assert flags["gated"] == _bootstrap(
            api_client, SDKKeyFactory(environment=env)
        )["gated"]

    def test_turning_visibility_off_takes_effect_immediately(
        self, auth_client, base, api_client, project, env, client_key
    ):
        _flag(project, env, "public-banner", visible=True)
        assert "public-banner" in _bootstrap(api_client, client_key)  # warms the cache

        auth_client.patch(
            f"{base}/public-banner/", {"client_side_available": False}, format="json"
        )

        assert "public-banner" not in _bootstrap(api_client, client_key)

    def test_warm_client_bootstrap_is_still_one_query(
        self, api_client, project, env, client_key, django_assert_num_queries
    ):
        _flag(project, env, "public-banner", visible=True)
        _flag(project, env, "secret-launch", visible=False)
        # Authenticate once and warm the cache outside the measured block.
        _bootstrap(api_client, client_key)
        api_client.credentials(HTTP_X_SDK_KEY=client_key._full_key)

        from apps.evaluation.services import FlagEvaluationService
        with django_assert_num_queries(1):
            results = FlagEvaluationService().evaluate_all(
                project_id=project.id, env_id=env.id, user_context=CONTEXT,
                client_side_only=True,
            )
        assert [r.flag_key for r in results] == ["public-banner"]


@pytest.mark.django_db
class TestSingleEvaluate:
    def test_client_key_gets_404_for_hidden_flag(self, api_client, project, env, client_key):
        _flag(project, env, "secret-launch", visible=False)
        assert _evaluate(api_client, client_key, "secret-launch").status_code == 404

    def test_client_key_evaluates_visible_flag(self, api_client, project, env, client_key):
        _flag(project, env, "public-banner", visible=True)
        assert _evaluate(api_client, client_key, "public-banner").status_code == 200

    def test_server_key_evaluates_hidden_flag(self, api_client, project, env, server_key):
        _flag(project, env, "secret-launch", visible=False)
        assert _evaluate(api_client, server_key, "secret-launch").status_code == 200

    def test_hidden_is_indistinguishable_from_missing(
        self, api_client, project, env, client_key
    ):
        """A 404 that differed from an unknown key's would confirm the flag exists."""
        _flag(project, env, "secret-launch", visible=False)
        hidden = _evaluate(api_client, client_key, "secret-launch")
        missing = _evaluate(api_client, client_key, "no-such-flag")
        assert hidden.status_code == missing.status_code == 404
        assert hidden.json() == missing.json()


@pytest.mark.django_db
class TestImpressions:
    def _send(self, api_client, sdk_key, flag_key):
        with patch("apps.evaluation.services.log_evaluations.delay"):
            return api_client.post(
                IMPRESSIONS,
                {"impressions": [
                    {"flag_key": flag_key, "result": True, "user_context": CONTEXT}
                ]},
                format="json",
                HTTP_X_SDK_KEY=sdk_key._full_key,
            ).json()

    def test_client_key_impression_for_hidden_flag_is_dropped(
        self, api_client, project, env, client_key
    ):
        _flag(project, env, "secret-launch", visible=False)
        assert self._send(api_client, client_key, "secret-launch") == {
            "accepted": 0, "dropped": ["secret-launch"]
        }

    def test_server_key_impression_for_hidden_flag_is_kept(
        self, api_client, project, env, server_key
    ):
        _flag(project, env, "secret-launch", visible=False)
        assert self._send(api_client, server_key, "secret-launch")["accepted"] == 1
