"""
Phase 3: SDK bulk download — POST /api/v1/sdk/flags/evaluate/

Every flag in the key's environment, resolved for one user context in a single
call. Two things have to hold, and the tests are split accordingly:

- it must agree with the per-flag endpoint on every targeting feature, and
- it must cost a fixed number of round trips, not one set per flag.
"""

import json
import pytest
from unittest.mock import patch

from conftest import (
    EnvironmentFactory,
    EnvironmentFlagFactory,
    FeatureFlagFactory,
    SDKKeyFactory,
    VariationFactory,
)

ENDPOINT = "/api/v1/sdk/flags/evaluate/"
SINGLE_ENDPOINT = "/api/v1/sdk/evaluate/"


def _bulk(api_client, sdk_key, **user_context):
    """No Celery patch: the bootstrap endpoint dispatches nothing at all.
    `TestBulkImpressionLogging` is what holds that."""
    return api_client.post(
        ENDPOINT,
        {"user_context": user_context},
        format="json",
        HTTP_X_SDK_KEY=sdk_key._full_key,
    )


def _on_off(flag):
    """Wire a flag with true/false variations and return them."""
    on = VariationFactory(flag=flag, name="on", value_type="boolean", value=True)
    off = VariationFactory(flag=flag, name="off", value_type="boolean", value=False)
    flag.fallthrough_variation, flag.off_variation = on, off
    flag.save(update_fields=["fallthrough_variation", "off_variation"])
    return on, off


@pytest.fixture
def env_with_flags(user, project, environment, sdk_key):
    """Three flags in one environment: fully on, fully off, and killed."""
    flags = {}
    for key, env_enabled, rollout in [
        ("alpha", True, 100),
        ("beta", True, 0),
        ("gamma", False, 100),
    ]:
        flag = FeatureFlagFactory(project=project, key=key, is_enabled=True)
        _on_off(flag)
        EnvironmentFlagFactory(
            feature_flag=flag, environment=environment,
            is_enabled=env_enabled, rollout_percentage=rollout,
        )
        flags[key] = flag
    return flags, sdk_key


@pytest.mark.django_db
class TestBulkEvaluateAuthentication:
    """Same SDK-key-only contract as the per-flag endpoint."""

    def test_valid_server_key_returns_200(self, api_client, environment_flag, sdk_key):
        assert _bulk(api_client, sdk_key, user_id="u1").status_code == 200

    def test_client_key_also_accepted(self, api_client, environment, environment_flag):
        client_key = SDKKeyFactory(environment=environment, key_type="client")
        assert _bulk(api_client, client_key, user_id="u1").status_code == 200

    def test_missing_sdk_key_returns_401(self, api_client):
        resp = api_client.post(ENDPOINT, {"user_context": {}}, format="json")
        assert resp.status_code == 401

    def test_invalid_sdk_key_returns_401(self, api_client):
        resp = api_client.post(
            ENDPOINT, {"user_context": {}}, format="json",
            HTTP_X_SDK_KEY="sdk_srv_totally_wrong",
        )
        assert resp.status_code == 401

    def test_jwt_token_not_accepted(self, api_client, user, environment_flag):
        from rest_framework_simplejwt.tokens import RefreshToken

        access = str(RefreshToken.for_user(user).access_token)
        resp = api_client.post(
            ENDPOINT, {"user_context": {}}, format="json",
            HTTP_AUTHORIZATION=f"Bearer {access}",
        )
        assert resp.status_code == 401

    def test_revoked_key_returns_401(self, api_client, sdk_key, environment_flag):
        sdk_key.is_active = False
        sdk_key.save()
        resp = api_client.post(
            ENDPOINT, {"user_context": {}}, format="json",
            HTTP_X_SDK_KEY=sdk_key._full_key,
        )
        assert resp.status_code == 401


@pytest.mark.django_db
class TestBulkEvaluateResponse:
    def test_returns_every_flag_keyed_by_flag_key(self, api_client, env_with_flags):
        _, sdk_key = env_with_flags
        body = _bulk(api_client, sdk_key, user_id="u1").json()
        assert set(body["flags"]) == {"alpha", "beta", "gamma"}

    def test_each_entry_carries_result_type_and_variation(self, api_client, env_with_flags):
        flags, sdk_key = env_with_flags
        entry = _bulk(api_client, sdk_key, user_id="u1").json()["flags"]["alpha"]
        assert entry["result"] is True
        assert entry["result_type"] == "boolean"
        assert entry["variation_id"] == flags["alpha"].fallthrough_variation_id

    def test_kill_switch_and_rollout_are_applied(self, api_client, env_with_flags):
        _, sdk_key = env_with_flags
        flags = _bulk(api_client, sdk_key, user_id="u1").json()["flags"]
        assert flags["alpha"]["result"] is True     # on, 100%
        assert flags["beta"]["result"] is False     # on, 0%
        assert flags["gamma"]["result"] is False    # killed in this environment

    def test_environment_name_comes_from_the_key(self, api_client, env_with_flags):
        _, sdk_key = env_with_flags
        body = _bulk(api_client, sdk_key, user_id="u1").json()
        assert body["environment"] == sdk_key.environment.name

    def test_user_context_is_optional(self, api_client, environment_flag, sdk_key):
        resp = api_client.post(
            ENDPOINT, {}, format="json", HTTP_X_SDK_KEY=sdk_key._full_key
        )
        assert resp.status_code == 200

    def test_empty_environment_returns_200_and_no_flags(self, api_client, sdk_key):
        """No flags configured is an empty result, not a 404 — unlike the
        per-flag endpoint, where a named flag that does not exist is an error."""
        resp = _bulk(api_client, sdk_key, user_id="u1")
        assert resp.status_code == 200
        assert resp.json()["flags"] == {}


@pytest.mark.django_db
class TestBulkEvaluateScope:
    """The SDK key's environment is the whole boundary."""

    def test_archived_flags_are_excluded(self, api_client, project, environment, sdk_key):
        live = FeatureFlagFactory(project=project, key="live")
        archived = FeatureFlagFactory(project=project, key="archived", is_archived=True)
        for flag in (live, archived):
            EnvironmentFlagFactory(
                feature_flag=flag, environment=environment, is_enabled=True
            )
        flags = _bulk(api_client, sdk_key, user_id="u1").json()["flags"]
        assert "live" in flags
        assert "archived" not in flags

    def test_flags_not_configured_in_this_environment_are_excluded(
        self, api_client, user, project, environment, sdk_key
    ):
        here = FeatureFlagFactory(project=project, key="here")
        EnvironmentFlagFactory(feature_flag=here, environment=environment, is_enabled=True)

        staging = EnvironmentFactory(project=project, name="staging")
        elsewhere = FeatureFlagFactory(project=project, key="elsewhere")
        EnvironmentFlagFactory(
            feature_flag=elsewhere, environment=staging, is_enabled=True
        )

        flags = _bulk(api_client, sdk_key, user_id="u1").json()["flags"]
        assert set(flags) == {"here"}

    def test_another_projects_flags_are_invisible(
        self, api_client, project, environment, sdk_key, other_user
    ):
        from conftest import personal_project_for

        mine = FeatureFlagFactory(project=project, key="mine")
        EnvironmentFlagFactory(feature_flag=mine, environment=environment, is_enabled=True)

        # Same flag key in another project, configured in that project's own
        # environment — must not bleed into this key's response.
        other_project = personal_project_for(other_user)
        theirs = FeatureFlagFactory(project=other_project, key="theirs")
        other_env = EnvironmentFactory(project=other_project, name='production')
        EnvironmentFlagFactory(
            feature_flag=theirs, environment=other_env, is_enabled=True
        )

        flags = _bulk(api_client, sdk_key, user_id="u1").json()["flags"]
        assert set(flags) == {"mine"}


# ---------------------------------------------------------------------------
# Agreement with the per-flag endpoint
# ---------------------------------------------------------------------------

@pytest.fixture
def rich_environment(user, project, environment, sdk_key):
    """One environment exercising every targeting layer at once.

    - pinned:       0% rollout, "alice" pinned on via an individual target
    - segmented:    0% rollout, rule "in segment beta" → on
    - partial:      rule "plan eq pro" at 50% rollout
    - gate:         plain 100% flag, used as a prerequisite
    - gated:        100% flag, but only when `gate` serves its on variation
    """
    from apps.flags.services import FlagService
    from apps.rules.models import Operator, Rule
    from apps.segments.services import SegmentService

    segments = SegmentService()
    segments.create_segment(project_key=project.key, user=user, key="beta", name="Beta")
    segments.set_target(
        project_key=project.key, key="beta", user=user,
        user_key="alice", excluded=False,
    )

    built = {}
    for key, rollout in [
        ("pinned", 0), ("segmented", 0), ("partial", 0), ("gate", 100), ("gated", 100),
    ]:
        flag = FeatureFlagFactory(project=project, key=key, is_enabled=True)
        on, off = _on_off(flag)
        EnvironmentFlagFactory(
            feature_flag=flag, environment=environment,
            is_enabled=True, rollout_percentage=rollout,
        )
        built[key] = (flag, on, off)

    FlagService().set_target(
        project_key=project.key, key="pinned", user=user,
        user_key="alice", variation_id=built["pinned"][1].id,
    )
    Rule.objects.create(
        flag=built["segmented"][0], attribute="", operator=Operator.IN_SEGMENT,
        value="beta", priority=1, serve_variation=built["segmented"][1],
    )
    Rule.objects.create(
        flag=built["partial"][0], attribute="plan", operator=Operator.EQUALS,
        value="pro", priority=1, serve_variation=built["partial"][1],
        rollout_percentage=50,
    )
    from apps.flags.models import FlagPrerequisite

    FlagPrerequisite.objects.create(
        flag=built["gated"][0],
        prerequisite_flag=built["gate"][0],
        required_variation=built["gate"][1],
    )
    return built, sdk_key


@pytest.mark.django_db
class TestBulkAgreesWithSingleEvaluate:
    """The bulk path must be the same engine, not a second implementation.

    Every flag, every user: whatever POST /sdk/evaluate/ says for one flag,
    POST /sdk/flags/evaluate/ must say for that flag in the batch.
    """

    @pytest.mark.parametrize("user_id", ["alice", "bob", "carol", "dave", "erin"])
    @pytest.mark.parametrize("plan", ["pro", "free"])
    def test_every_flag_matches_the_per_flag_endpoint(
        self, api_client, rich_environment, user_id, plan
    ):
        built, sdk_key = rich_environment

        bulk = _bulk(api_client, sdk_key, user_id=user_id, plan=plan).json()["flags"]

        for key in built:
            with patch("apps.sdk.views.log_evaluation.delay"):
                single = api_client.post(
                    SINGLE_ENDPOINT,
                    {"flag_key": key, "user_context": {"user_id": user_id, "plan": plan}},
                    format="json",
                    HTTP_X_SDK_KEY=sdk_key._full_key,
                )
            assert single.status_code == 200
            assert bulk[key]["result"] == single.json()["result"], (
                f"bulk and single disagree on {key!r} for {user_id!r}/{plan!r}"
            )

    def test_prerequisite_gate_is_enforced_in_bulk(self, api_client, rich_environment):
        """`gated` follows `gate`, resolved through the preloaded map rather
        than a fresh lookup — turning the gate off must switch it off too."""
        built, sdk_key = rich_environment
        assert _bulk(api_client, sdk_key, user_id="bob").json()["flags"]["gated"]["result"] is True

        gate_flag, _, gate_off = built["gate"]
        env_flag = gate_flag.environment_states.get()
        env_flag.is_enabled = False
        env_flag.save(update_fields=["is_enabled"])
        from apps.flags.services import FlagService

        FlagService.invalidate_flag_caches(gate_flag)

        flags = _bulk(api_client, sdk_key, user_id="bob").json()["flags"]
        assert flags["gate"]["result"] is False
        assert flags["gated"]["result"] is False, "an unmet gate must switch the dependent off"

    def test_bulk_warm_leaves_a_cache_the_single_path_reuses(
        self, api_client, rich_environment
    ):
        """A bulk warm must write the payload a single evaluation would have
        written — otherwise the two paths drift apart via the shared cache."""
        from django.core.cache import cache

        from apps.evaluation.services import FlagEvaluationService

        built, sdk_key = rich_environment
        project_id = sdk_key.environment.project_id
        env_id = sdk_key.environment_id

        service = FlagEvaluationService()
        single_payloads = {}
        for key in built:
            service.evaluate(
                flag_key=key, project_id=project_id,
                user_context={"user_id": "alice"}, env_id=env_id,
            )
            single_payloads[key] = cache.get(f"flags:{project_id}:{env_id}:{key}")

        cache.delete_many([f"flags:{project_id}:{env_id}:{key}" for key in built])
        service.evaluate_all(
            project_id=project_id, env_id=env_id, user_context={"user_id": "alice"}
        )

        for key in built:
            assert cache.get(f"flags:{project_id}:{env_id}:{key}") == single_payloads[key], (
                f"bulk warm wrote a different cache payload for {key!r}"
            )


# ---------------------------------------------------------------------------
# Round-trip cost — the reason this endpoint exists
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBulkEvaluateCost:
    """A bulk evaluation must cost a fixed number of round trips.

    If any of these regress, the endpoint has quietly become N single
    evaluations in a trench coat and there is no reason to prefer it.
    """

    @staticmethod
    def _environment_with(n, key_prefix):
        """A fresh project + environment holding `n` flags, each with a rule
        referencing a shared segment (so segment resolution is in play)."""
        from conftest import (
            EnvironmentFactory,
            ProjectFactory,
            UserFactory,
            MembershipFactory,
        )
        from apps.rules.models import Operator, Rule
        from apps.segments.services import SegmentService
        from apps.organizations.models import Role

        owner = UserFactory()
        project = ProjectFactory()
        MembershipFactory(organization=project.organization, user=owner, role=Role.OWNER)
        environment = EnvironmentFactory(project=project, name="production")

        segments = SegmentService()
        segments.create_segment(
            project_key=project.key, user=owner, key=f"{key_prefix}-seg", name="S"
        )
        segments.set_target(
            project_key=project.key, key=f"{key_prefix}-seg", user=owner,
            user_key="alice", excluded=False,
        )

        for i in range(n):
            flag = FeatureFlagFactory(project=project, key=f"{key_prefix}-{i}")
            on, _ = _on_off(flag)
            EnvironmentFlagFactory(
                feature_flag=flag, environment=environment,
                is_enabled=True, rollout_percentage=0,
            )
            Rule.objects.create(
                flag=flag, attribute="", operator=Operator.IN_SEGMENT,
                value=f"{key_prefix}-seg", priority=1, serve_variation=on,
            )
        return project, environment

    @classmethod
    def _cold_queries_for(cls, n, key_prefix):
        from django.core.cache import cache
        from django.db import connection, reset_queries
        from django.test.utils import override_settings

        from apps.evaluation.services import FlagEvaluationService

        project, environment = cls._environment_with(n, key_prefix)
        cache.delete_many([
            f"flags:{project.id}:{environment.id}:{key_prefix}-{i}" for i in range(n)
        ])
        with override_settings(DEBUG=True):
            reset_queries()
            FlagEvaluationService().evaluate_all(
                project_id=project.id, env_id=environment.id,
                user_context={"user_id": "alice"},
            )
            return len(connection.queries)

    def test_cold_cache_query_count_does_not_grow_with_flag_count(self):
        """Warming N misses must be a fixed number of queries, not N of them."""
        few = self._cold_queries_for(2, "cost-few")
        many = self._cold_queries_for(20, "cost-many")
        assert few == many, (
            f"bulk warm is linear in flag count: {few} queries for 2 flags, {many} for 20"
        )

    def test_warm_cache_costs_exactly_one_query(self, django_assert_num_queries):
        """The one unavoidable query is the flag-key index: a warm cache knows
        each flag's config but not which flags exist in the environment."""
        from apps.evaluation.services import FlagEvaluationService

        project, environment = self._environment_with(20, "cost-warm")
        service = FlagEvaluationService()
        context = {"user_id": "alice"}
        service.evaluate_all(
            project_id=project.id, env_id=environment.id, user_context=context
        )  # prime

        with django_assert_num_queries(1):
            results = service.evaluate_all(
                project_id=project.id, env_id=environment.id, user_context=context
            )
        assert len(results) == 20

    def test_warm_cache_reads_redis_once_not_once_per_flag(self):
        """One `get_many`, zero per-flag `get`s.

        The per-flag `get` is what the preloaded map exists to avoid; without
        it this is 20 Redis round trips instead of 1.
        """
        from django.core.cache import cache as real_cache
        from unittest.mock import MagicMock

        from apps.evaluation.services import FlagEvaluationService

        project, environment = self._environment_with(20, "cost-redis")
        service = FlagEvaluationService()
        context = {"user_id": "alice"}
        service.evaluate_all(
            project_id=project.id, env_id=environment.id, user_context=context
        )  # prime

        spy = MagicMock(wraps=real_cache)
        with patch("apps.evaluation.services.cache", spy):
            service.evaluate_all(
                project_id=project.id, env_id=environment.id, user_context=context
            )

        assert spy.get_many.call_count == 1
        assert spy.get.call_count == 0, (
            f"bulk evaluation fell back to per-flag cache reads ({spy.get.call_count} of them)"
        )

    def test_prerequisite_chains_resolve_from_the_preloaded_map(self, user, project, environment):
        """A gate flag is already in the batch, so resolving it must not cost
        another Redis read on top of the one `get_many`."""
        from django.core.cache import cache as real_cache
        from unittest.mock import MagicMock

        from apps.flags.models import FlagPrerequisite
        from apps.evaluation.services import FlagEvaluationService

        previous = None
        for i in range(5):
            flag = FeatureFlagFactory(project=project, key=f"chain-{i}")
            on, _ = _on_off(flag)
            EnvironmentFlagFactory(
                feature_flag=flag, environment=environment,
                is_enabled=True, rollout_percentage=100,
            )
            if previous is not None:
                FlagPrerequisite.objects.create(
                    flag=flag, prerequisite_flag=previous[0], required_variation=previous[1]
                )
            previous = (flag, on)

        service = FlagEvaluationService()
        context = {"user_id": "alice"}
        service.evaluate_all(
            project_id=project.id, env_id=environment.id, user_context=context
        )  # prime

        spy = MagicMock(wraps=real_cache)
        with patch("apps.evaluation.services.cache", spy):
            results = service.evaluate_all(
                project_id=project.id, env_id=environment.id, user_context=context
            )

        assert {r.flag_key: r.result for r in results} == {
            f"chain-{i}": True for i in range(5)
        }
        assert spy.get.call_count == 0, "prerequisite resolution bypassed the preloaded map"


# ---------------------------------------------------------------------------
# Impression logging
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBulkImpressionLogging:
    """A bootstrap is a download, not a read.

    It resolves every flag in the environment, but the app may go on to use
    three of fifty. Logging all fifty as impressions inflates `EvaluationLog`
    — a table with no rollup — with rows that record a fetch nobody consumed.
    Impressions arrive through the batching endpoint instead, where the SDK
    reports what it actually read.
    """

    def test_bootstrap_dispatches_no_impression_task(self, api_client, env_with_flags):
        _, sdk_key = env_with_flags
        with patch("apps.evaluation.tasks.log_evaluations.delay") as batch, \
             patch("apps.evaluation.tasks.log_evaluation.delay") as single:
            resp = api_client.post(
                ENDPOINT, {"user_context": {"user_id": "u1"}}, format="json",
                HTTP_X_SDK_KEY=sdk_key._full_key,
            )
        assert resp.status_code == 200
        assert not batch.called, "bootstrap logged impressions for flags nobody read yet"
        assert not single.called

    def test_bootstrap_writes_no_evaluation_rows(self, api_client, env_with_flags):
        """The task is mocked in the test above; this one checks the real
        outcome — that nothing lands in the table."""
        from apps.evaluation.models import EvaluationLog

        _, sdk_key = env_with_flags
        before = EvaluationLog.objects.count()
        api_client.post(
            ENDPOINT, {"user_context": {"user_id": "u1"}}, format="json",
            HTTP_X_SDK_KEY=sdk_key._full_key,
        )
        assert EvaluationLog.objects.count() == before

    def test_per_flag_endpoint_still_logs(self, api_client, environment_flag, sdk_key):
        """Removing impression logging from the bootstrap must not remove it
        from the endpoint that genuinely serves one flag to one caller."""
        with patch("apps.sdk.views.log_evaluation.delay") as delay:
            api_client.post(
                SINGLE_ENDPOINT,
                {"flag_key": environment_flag.feature_flag.key,
                 "user_context": {"user_id": "u1"}},
                format="json",
                HTTP_X_SDK_KEY=sdk_key._full_key,
            )
        delay.assert_called_once()


@pytest.mark.django_db
class TestBatchIngestPrimitive:
    """`log_evaluations` stays as the ingest primitive for the batching
    endpoint (Phase 3, item 2). It is not wired to a view yet, so these test
    it directly — otherwise the batching work inherits it untested."""

    def test_writes_one_row_per_evaluation_in_one_insert(self, flag):
        from django.db import connection, reset_queries
        from django.test.utils import override_settings

        from apps.evaluation.models import EvaluationLog
        from apps.evaluation.tasks import log_evaluations

        with override_settings(DEBUG=True):
            reset_queries()
            log_evaluations(
                evaluations=[
                    {"flag_id": flag.id, "result": True},
                    {"flag_id": flag.id, "result": False},
                    {"flag_id": flag.id, "result": "variant-b"},
                ],
                user_id=None,
                context_data={"user_id": "u1"},
            )
            inserts = [q for q in connection.queries if "INSERT" in q["sql"].upper()]

        assert EvaluationLog.objects.count() == 3
        assert len(inserts) == 1, f"expected one bulk insert, got {len(inserts)}"

    def test_empty_batch_is_a_no_op(self, db):
        from apps.evaluation.models import EvaluationLog
        from apps.evaluation.tasks import log_evaluations

        log_evaluations(evaluations=[], user_id=None, context_data={})
        assert EvaluationLog.objects.count() == 0


@pytest.mark.django_db
class TestEvaluationResultsStayJsonSafe:
    """`CELERY_TASK_SERIALIZER` is "json" and the cached flag config holds
    Python sets (segment membership), which json.dumps cannot encode.

    The bootstrap endpoint no longer dispatches a task, so this pins the
    durable invariant underneath that: what `evaluate_all` hands back is
    already safe to put on a queue. The batching endpoint will send exactly
    this, and a config leak into an `EvaluationResult` would surface in the
    async path where it is easy to miss.
    """

    def test_evaluate_all_results_are_json_encodable(self, rich_environment):
        from apps.evaluation.services import FlagEvaluationService

        _, sdk_key = rich_environment
        results = FlagEvaluationService().evaluate_all(
            project_id=sdk_key.environment.project_id,
            env_id=sdk_key.environment_id,
            user_context={"user_id": "alice"},
        )
        assert results

        # Raises TypeError if a set (or anything else Celery's json serializer
        # cannot encode) has leaked out of the flag config into a result.
        json.dumps([
            {"flag_id": r.flag_id, "result": r.result, "variation_id": r.variation_id}
            for r in results
        ])

    def test_response_body_is_json_encodable(self, api_client, rich_environment):
        _, sdk_key = rich_environment
        resp = api_client.post(
            ENDPOINT, {"user_context": {"user_id": "alice"}}, format="json",
            HTTP_X_SDK_KEY=sdk_key._full_key,
        )
        assert resp.status_code == 200
        json.dumps(resp.json())
