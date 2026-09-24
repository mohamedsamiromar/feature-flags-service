"""
Phase 3: SDK config download — GET /api/v1/sdk/flags/config/

The environment's raw ruleset, for a server-side SDK that evaluates in-process.
Specified in SDK_CONFIG_SPEC.md.

The risk this endpoint creates is that every SDK reimplements the engine, so
what is asserted here is the *contract*: that the payload carries everything an
SDK needs to reproduce the server's answer, in the shape the spec promises, and
that it never leaks a user list to a browser key.
"""

import json

import pytest
from django.db.models import F
from rest_framework import status

from apps.environment.models import Environment
from apps.evaluation.services import CONFIG_FORMAT_VERSION
from apps.rules.models import Operator
from apps.sdk_keys.models import SDKKey
from apps.segments.models import Segment, SegmentRule, SegmentTarget

from conftest import (
    EnvironmentFlagFactory,
    FeatureFlagFactory,
    SDKKeyFactory,
    VariationFactory,
)

ENDPOINT = "/api/v1/sdk/flags/config/"


def _get(api_client, sdk_key, **headers):
    return api_client.get(ENDPOINT, HTTP_X_SDK_KEY=sdk_key._full_key, **headers)


def _on_off(flag):
    on = VariationFactory(flag=flag, name="on", value_type="boolean", value=True)
    off = VariationFactory(flag=flag, name="off", value_type="boolean", value=False)
    flag.fallthrough_variation, flag.off_variation = on, off
    flag.save(update_fields=["fallthrough_variation", "off_variation"])
    return on, off


@pytest.fixture
def configured_env(project, environment, sdk_key):
    """One flag with a rule, an individual target, and a segment reference."""
    segment = Segment.objects.create(project=project, key="beta", name="Beta")
    SegmentTarget.objects.create(segment=segment, user_key="alice", excluded=False)
    SegmentTarget.objects.create(segment=segment, user_key="carol", excluded=True)
    SegmentRule.objects.create(
        segment=segment, attribute="plan", operator=Operator.EQUALS, value="pro"
    )

    flag = FeatureFlagFactory(project=project, key="dark-mode", is_enabled=True)
    on, off = _on_off(flag)
    flag.rules.create(
        attribute="country", operator=Operator.EQUALS, value="EG",
        priority=1, rollout_percentage=50, serve_variation=on,
    )
    flag.rules.create(
        attribute="", operator=Operator.IN_SEGMENT, value="beta", priority=2,
    )
    flag.targets.create(user_key="dave", variation=off)
    EnvironmentFlagFactory(
        feature_flag=flag, environment=environment,
        is_enabled=True, rollout_percentage=20,
    )
    return {"flag": flag, "on": on, "off": off, "segment": segment}


# ---------------------------------------------------------------------------
# Wire format
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestWireFormat:
    def test_returns_the_environments_ruleset(self, api_client, sdk_key, configured_env):
        response = _get(api_client, sdk_key)

        assert response.status_code == status.HTTP_200_OK
        assert response.data["format_version"] == CONFIG_FORMAT_VERSION
        assert response.data["environment"] == sdk_key.environment.name
        assert "dark-mode" in response.data["flags"]

    def test_flag_carries_everything_needed_to_evaluate_locally(
        self, api_client, sdk_key, configured_env
    ):
        flag = _get(api_client, sdk_key).data["flags"]["dark-mode"]

        assert flag["is_enabled"] is True
        assert flag["rollout_percentage"] == 20
        assert flag["off_variation"]["id"] == configured_env["off"].id
        assert flag["fallthrough_variation"]["id"] == configured_env["on"].id
        assert len(flag["rules"]) == 2

    def test_rules_carry_their_id_because_it_salts_bucketing(
        self, api_client, sdk_key, configured_env
    ):
        """Without the rule id an SDK cannot reproduce rule-level rollout at all."""
        rules = _get(api_client, sdk_key).data["flags"]["dark-mode"]["rules"]

        assert all(rule["id"] for rule in rules)
        assert rules[0]["rollout_percentage"] == 50

    def test_rules_are_ordered_by_priority(self, api_client, sdk_key, configured_env):
        """First match wins outright, so the order is part of the contract."""
        rules = _get(api_client, sdk_key).data["flags"]["dark-mode"]["rules"]

        assert [rule["priority"] for rule in rules] == sorted(
            rule["priority"] for rule in rules
        )

    def test_targets_map_user_key_to_variation_id_not_a_dict(
        self, api_client, sdk_key, configured_env
    ):
        """The variations are already in the payload — repeating them is waste."""
        flag = _get(api_client, sdk_key).data["flags"]["dark-mode"]

        assert flag["targets"] == {"dave": configured_env["off"].id}

    def test_segments_are_lifted_to_a_top_level_map(
        self, api_client, sdk_key, configured_env
    ):
        """Not duplicated per referencing flag — a large segment would be repeated."""
        data = _get(api_client, sdk_key).data

        assert "beta" in data["segments"]
        assert "segments" not in data["flags"]["dark-mode"]

    def test_segment_sets_become_sorted_arrays(self, api_client, sdk_key, configured_env):
        """The cache holds Python sets; json.dumps cannot encode one."""
        segment = _get(api_client, sdk_key).data["segments"]["beta"]

        assert segment["included"] == ["alice"]
        assert segment["excluded"] == ["carol"]
        assert segment["rules"][0]["value"] == "pro"

    def test_payload_is_json_serialisable(self, api_client, sdk_key, configured_env):
        """The set-to-array conversion is the whole reason this can be asserted."""
        response = _get(api_client, sdk_key)

        assert json.loads(response.content)["flags"]["dark-mode"]

    def test_shared_segment_appears_once_for_two_flags(
        self, api_client, project, environment, sdk_key, configured_env
    ):
        other = FeatureFlagFactory(project=project, key="light-mode", is_enabled=True)
        _on_off(other)
        other.rules.create(
            attribute="", operator=Operator.IN_SEGMENT, value="beta", priority=1
        )
        EnvironmentFlagFactory(
            feature_flag=other, environment=environment, is_enabled=True
        )

        data = _get(api_client, sdk_key).data

        assert set(data["flags"]) == {"dark-mode", "light-mode"}
        assert list(data["segments"]) == ["beta"]

    def test_prerequisites_are_carried_with_the_required_variation_id(
        self, api_client, project, environment, sdk_key, configured_env
    ):
        """Gates compare variation identity, never value."""
        gate = FeatureFlagFactory(project=project, key="gate", is_enabled=True)
        gate_on, _ = _on_off(gate)
        EnvironmentFlagFactory(
            feature_flag=gate, environment=environment, is_enabled=True
        )
        configured_env["flag"].prerequisites.create(
            prerequisite_flag=gate, required_variation=gate_on
        )

        flag = _get(api_client, sdk_key).data["flags"]["dark-mode"]

        assert flag["prerequisites"] == [
            {"flag_key": "gate", "required_variation_id": gate_on.id}
        ]


# ---------------------------------------------------------------------------
# Scoping
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestScoping:
    def test_archived_flags_are_excluded(
        self, api_client, project, environment, sdk_key, configured_env
    ):
        configured_env["flag"].is_archived = True
        configured_env["flag"].save(update_fields=["is_archived"])

        assert _get(api_client, sdk_key).data["flags"] == {}

    def test_flags_with_no_state_in_this_environment_are_excluded(
        self, api_client, project, environment, sdk_key, configured_env
    ):
        """No EnvironmentFlag row means the flag is not configured here."""
        FeatureFlagFactory(project=project, key="unconfigured", is_enabled=True)

        assert "unconfigured" not in _get(api_client, sdk_key).data["flags"]

    def test_another_environments_flags_are_excluded(
        self, api_client, project, environment, sdk_key, configured_env
    ):
        staging = Environment.objects.create(project=project, name="staging")
        other = FeatureFlagFactory(project=project, key="staging-only", is_enabled=True)
        EnvironmentFlagFactory(
            feature_flag=other, environment=staging, is_enabled=True
        )

        assert "staging-only" not in _get(api_client, sdk_key).data["flags"]


# ---------------------------------------------------------------------------
# Security — the reason this endpoint refuses client keys
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestKeyType:
    def test_client_key_is_refused(self, api_client, environment, configured_env):
        """The payload holds real user identifiers; client keys ship to browsers."""
        client_key = SDKKeyFactory(
            environment=environment, key_type=SDKKey.KeyType.CLIENT
        )

        response = _get(api_client, client_key)

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_a_refused_client_key_gets_no_user_identifiers(
        self, api_client, environment, configured_env
    ):
        client_key = SDKKeyFactory(
            environment=environment, key_type=SDKKey.KeyType.CLIENT
        )

        body = _get(api_client, client_key).content.decode()

        assert "alice" not in body
        assert "dave" not in body

    def test_server_key_is_accepted(self, api_client, sdk_key, configured_env):
        assert _get(api_client, sdk_key).status_code == status.HTTP_200_OK

    def test_missing_key_is_401(self, api_client, configured_env):
        assert api_client.get(ENDPOINT).status_code == status.HTTP_401_UNAUTHORIZED

    def test_revoked_key_is_401(self, api_client, sdk_key, configured_env):
        sdk_key.is_active = False
        sdk_key.save(update_fields=["is_active"])

        assert _get(api_client, sdk_key).status_code == status.HTTP_401_UNAUTHORIZED


# ---------------------------------------------------------------------------
# Versioning and conditional requests
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestConfigVersion:
    def test_etag_carries_the_config_version(self, api_client, sdk_key, configured_env):
        response = _get(api_client, sdk_key)

        assert response["ETag"] == f'"{response.data["config_version"]}"'

    def test_matching_if_none_match_returns_304_with_no_body(
        self, api_client, sdk_key, configured_env
    ):
        """The common case for a poller."""
        first = _get(api_client, sdk_key)

        second = _get(api_client, sdk_key, HTTP_IF_NONE_MATCH=first["ETag"])

        assert second.status_code == status.HTTP_304_NOT_MODIFIED
        assert not second.content

    def test_304_still_carries_the_etag(self, api_client, sdk_key, configured_env):
        """Without it a poller has nothing to send on the next request."""
        first = _get(api_client, sdk_key)

        second = _get(api_client, sdk_key, HTTP_IF_NONE_MATCH=first["ETag"])

        assert second["ETag"] == first["ETag"]

    def test_weak_validator_matches(self, api_client, sdk_key, configured_env):
        """Intermediaries legitimately rewrite ETags as weak."""
        first = _get(api_client, sdk_key)

        second = _get(api_client, sdk_key, HTTP_IF_NONE_MATCH=f'W/{first["ETag"]}')

        assert second.status_code == status.HTTP_304_NOT_MODIFIED

    def test_star_matches(self, api_client, sdk_key, configured_env):
        response = _get(api_client, sdk_key, HTTP_IF_NONE_MATCH="*")

        assert response.status_code == status.HTTP_304_NOT_MODIFIED

    def test_list_of_etags_matches(self, api_client, sdk_key, configured_env):
        first = _get(api_client, sdk_key)

        second = _get(
            api_client, sdk_key, HTTP_IF_NONE_MATCH=f'"1", {first["ETag"]}, "999"'
        )

        assert second.status_code == status.HTTP_304_NOT_MODIFIED

    def test_non_matching_if_none_match_returns_the_body(
        self, api_client, sdk_key, configured_env
    ):
        """Derived from the live version rather than hard-coded: a fresh
        environment sits at version 1, so `"1"` would legitimately match."""
        current = _get(api_client, sdk_key).data["config_version"]

        response = _get(api_client, sdk_key, HTTP_IF_NONE_MATCH=f'"{current - 1}"')

        assert response.status_code == status.HTTP_200_OK
        assert response.data["flags"]

    def test_toggling_a_flag_moves_the_version(
        self, api_client, sdk_key, environment, configured_env, auth_client, project
    ):
        """A poller must be told that the kill switch flipped."""
        before = _get(api_client, sdk_key).data["config_version"]

        auth_client.post(
            f"/api/v1/projects/{project.key}/flags/dark-mode/toggle/",
            {"environment": environment.name},
            format="json",
        )

        after = _get(api_client, sdk_key)
        assert after.data["config_version"] > before
        assert after.status_code == status.HTTP_200_OK

    def test_a_rule_change_moves_the_version(
        self, api_client, sdk_key, configured_env, auth_client
    ):
        """Rules live outside the flag service but change what is served."""
        before = _get(api_client, sdk_key).data["config_version"]
        rule = configured_env["flag"].rules.first()

        auth_client.patch(
            f"/api/v1/rules/{rule.id}/", {"value": "US"}, format="json"
        )

        after = _get(api_client, sdk_key)
        assert after.data["config_version"] > before
        assert after.data["flags"]["dark-mode"]["rules"][0]["value"] == "US"

    def test_a_segment_edit_moves_the_version(
        self, api_client, sdk_key, project, configured_env, auth_client
    ):
        """Segment edits fan out to every flag referencing them."""
        before = _get(api_client, sdk_key).data["config_version"]

        auth_client.put(
            f"/api/v1/projects/{project.key}/segments/beta/targets/",
            {"user_key": "erin", "excluded": False},
            format="json",
        )

        after = _get(api_client, sdk_key)
        assert after.data["config_version"] > before
        assert "erin" in after.data["segments"]["beta"]["included"]

    def test_stale_etag_after_a_change_returns_the_new_config(
        self, api_client, sdk_key, environment, configured_env, auth_client, project
    ):
        """The whole point: an SDK holding an old version gets the new body."""
        first = _get(api_client, sdk_key)
        auth_client.post(
            f"/api/v1/projects/{project.key}/flags/dark-mode/toggle/",
            {"environment": environment.name},
            format="json",
        )

        second = _get(api_client, sdk_key, HTTP_IF_NONE_MATCH=first["ETag"])

        assert second.status_code == status.HTTP_200_OK
        assert second.data["config_version"] != first.data["config_version"]

    def test_version_bump_is_atomic_under_concurrent_writes(self, environment):
        """An F() update, not a read-then-write: two writers must not both
        read 1 and both store 2, leaving an SDK on stale content under a
        version it thinks is current."""
        from apps.environment.queries import EnvironmentQuery

        start = Environment.objects.get(pk=environment.pk).config_version
        for _ in range(5):
            EnvironmentQuery.bump_config_versions([environment.pk])

        assert Environment.objects.get(pk=environment.pk).config_version == start + 5

    def test_bumping_an_empty_set_is_a_no_op(self, environment):
        from apps.environment.queries import EnvironmentQuery

        before = Environment.objects.get(pk=environment.pk).config_version
        EnvironmentQuery.bump_config_versions([])

        assert Environment.objects.get(pk=environment.pk).config_version == before


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCaching:
    def test_repeat_fetch_hits_the_version_keyed_cache(
        self, api_client, sdk_key, configured_env, django_assert_num_queries
    ):
        _get(api_client, sdk_key)

        # Only the SDK key lookup and its last_used_at update remain; the
        # payload itself comes from the version-keyed cache entry.
        with django_assert_num_queries(2):
            response = _get(api_client, sdk_key)

        assert response.status_code == status.HTTP_200_OK

    def test_a_version_bump_makes_the_old_entry_unreachable(
        self, api_client, sdk_key, environment, configured_env
    ):
        """Version-keyed, so nothing ever has to evict it explicitly."""
        first = _get(api_client, sdk_key).data
        Environment.objects.filter(pk=environment.pk).update(
            config_version=F("config_version") + 1
        )
        environment.refresh_from_db()

        second = _get(api_client, sdk_key).data

        assert second["config_version"] == first["config_version"] + 1
