"""
Cache coherence under a concurrent write.

The read path is cache-aside: `get → miss → read Postgres → set`. The write path
is `save → evict → bump config_version`. Nothing stops a reader that read the
row *before* the write from setting its copy *after* the eviction:

    reader:  cache miss → loads the OLD row
    writer:  saves → evicts (nothing there yet) → bumps config_version to v+1
    reader:  cache.set(OLD payload)       ← served for up to FLAG_CACHE_TTL

The config download makes it worse. Its payload for v+1 is built from those
per-flag entries, so the stale copy gets cached under v+1. The ETag is only
the version, so an SDK holding "v+1" gets a 304 on every later poll, even
after the cache expires, until some unrelated change bumps the version again.

The interleaving is forced deterministically: `_build_flag_data` runs after the
reader has loaded its row and before it calls `cache.set`, so the writer is
injected there. Only that seam is patched; the ORM, Redis, and the HTTP
stack are real.

Each race test is `xfail(strict=True)`: it asserts the correct behaviour and
fails today. When the race is fixed it XPASSes, strict mode turns that into a
failure, and the marker has to be removed. Every one has a control twin that
runs the same flow without the injected write and passes, so the harness
itself is not what makes the race test fail.
"""

from unittest.mock import patch

import pytest

from apps.evaluation.services import FlagEvaluationService
from apps.flags.services import FlagService

from conftest import EnvironmentFlagFactory, FeatureFlagFactory, VariationFactory

EVALUATE = "/api/v1/sdk/evaluate/"
CONFIG = "/api/v1/sdk/flags/config/"

race = pytest.mark.xfail(
    strict=True,
    # Only a failed assertion counts as reproducing the race. A setup error
    # must still fail the suite, not pass as an expected failure.
    raises=AssertionError,
    reason="cache-aside race: a reader that loaded the row before a write "
           "re-caches it after the eviction",
)


@pytest.fixture
def live_flag(project, environment):
    """A flag that is on in `environment` for everyone."""
    flag = FeatureFlagFactory(project=project, key="checkout-v2", is_enabled=True)
    on = VariationFactory(flag=flag, name="on", value_type="boolean", value=True)
    off = VariationFactory(flag=flag, name="off", value_type="boolean", value=False)
    flag.fallthrough_variation, flag.off_variation = on, off
    flag.save(update_fields=["fallthrough_variation", "off_variation"])
    EnvironmentFlagFactory(
        feature_flag=flag, environment=environment, is_enabled=True, rollout_percentage=100,
    )
    return flag


@pytest.fixture
def kill_switch(user, project, environment, live_flag):
    """Flip the flag off in `environment` through the real service path."""
    def flip():
        FlagService().toggle_environment(project.key, live_flag.key, user, environment.name)
    return flip


def _write_during_first_cache_fill(writer):
    """Run `writer` once, after a reader loaded its row and before it caches it."""
    original = FlagEvaluationService._build_flag_data
    fired = []

    def build_then_write(env_flag, rules, segments):
        payload = original(env_flag, rules, segments)
        if not fired:
            fired.append(True)
            writer()
        return payload

    return patch.object(
        FlagEvaluationService, "_build_flag_data", new=staticmethod(build_then_write)
    )


def _evaluate(api_client, sdk_key, flag_key):
    with patch("apps.sdk.views.log_evaluation.delay"):
        response = api_client.post(
            EVALUATE,
            {"flag_key": flag_key, "user_context": {"user_id": "u1"}},
            format="json",
            HTTP_X_SDK_KEY=sdk_key._full_key,
        )
    assert response.status_code == 200, response.content
    return response.json()["result"]


def _poll(api_client, sdk_key, etag=None):
    headers = {"HTTP_IF_NONE_MATCH": etag} if etag else {}
    return api_client.get(CONFIG, HTTP_X_SDK_KEY=sdk_key._full_key, **headers)


def _expire_everything():
    """Stand-in for FLAG_CACHE_TTL elapsing on every entry."""
    from django.core.cache import cache
    cache.clear()


# ---------------------------------------------------------------------------
# Per-flag evaluation: POST /sdk/evaluate/
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestEvaluateAfterConcurrentKillSwitch:
    def test_control_kill_switch_without_a_racing_reader(
        self, api_client, sdk_key, live_flag, kill_switch
    ):
        assert _evaluate(api_client, sdk_key, live_flag.key) is True
        kill_switch()
        assert _evaluate(api_client, sdk_key, live_flag.key) is False

    @race
    def test_kill_switch_is_honoured_by_the_next_request(
        self, api_client, sdk_key, live_flag, kill_switch
    ):
        # The racing request may itself see the old value; it was in flight
        # before the write. Every request after the write must not.
        with _write_during_first_cache_fill(kill_switch):
            _evaluate(api_client, sdk_key, live_flag.key)

        assert _evaluate(api_client, sdk_key, live_flag.key) is False


# ---------------------------------------------------------------------------
# Config download: GET /sdk/flags/config/
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestConfigDownloadAfterConcurrentKillSwitch:
    def test_control_new_version_carries_the_kill_switch(
        self, api_client, sdk_key, environment, live_flag, kill_switch
    ):
        before = _poll(api_client, sdk_key)
        kill_switch()
        after = _poll(api_client, sdk_key, etag=before["ETag"])

        assert after.status_code == 200
        assert after["ETag"] != before["ETag"]
        assert after.data["flags"][live_flag.key]["is_enabled"] is False

    @race
    def test_new_version_carries_the_kill_switch(
        self, api_client, sdk_key, environment, live_flag, kill_switch
    ):
        before = _poll(api_client, sdk_key)
        _expire_everything()  # so the next reader has to fill from Postgres
        with _write_during_first_cache_fill(kill_switch):
            _evaluate(api_client, sdk_key, live_flag.key)

        after = _poll(api_client, sdk_key, etag=before["ETag"])

        # Sanity: the write did land and did bump the version, so the
        # payload below claims to describe the post-write state.
        environment.refresh_from_db()
        assert after["ETag"] == f'"{environment.config_version}"' != before["ETag"]
        assert after.data["flags"][live_flag.key]["is_enabled"] is False

    def test_control_sdk_holding_the_current_version_is_current(
        self, api_client, sdk_key, live_flag, kill_switch
    ):
        kill_switch()
        held = _poll(api_client, sdk_key)
        _expire_everything()

        assert _poll(api_client, sdk_key, etag=held["ETag"]).status_code == 304
        assert held.data == _poll(api_client, sdk_key).data

    @race
    def test_sdk_is_not_left_on_stale_config_after_the_cache_expires(
        self, api_client, sdk_key, live_flag, kill_switch
    ):
        """The worst case: stale, and it does not heal.

        The SDK stores whatever body came with the ETag. Once the server
        caches expire, the server itself serves the right config, but the SDK
        polls with the ETag it holds and gets 304, forever, because the
        version does not move again until some unrelated change.
        """
        with _write_during_first_cache_fill(kill_switch):
            _evaluate(api_client, sdk_key, live_flag.key)
        held = _poll(api_client, sdk_key)

        _expire_everything()
        fresh = _poll(api_client, sdk_key)
        assert fresh["ETag"] == held["ETag"]  # no further write happened
        assert fresh.data["flags"][live_flag.key]["is_enabled"] is False

        still_current = _poll(api_client, sdk_key, etag=held["ETag"]).status_code == 304
        holds_stale_body = held.data != fresh.data
        assert not (still_current and holds_stale_body), (
            "SDK is told its stale config is current"
        )
