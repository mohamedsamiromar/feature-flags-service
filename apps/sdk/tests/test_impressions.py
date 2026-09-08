"""
Phase 3: impression batching — POST /api/v1/sdk/impressions/

Local evaluation is invisible without this. An SDK working from
`GET /sdk/flags/config/` never calls `POST /sdk/evaluate/`, and the bootstrap
endpoint deliberately logs nothing, so every flag served either way would be
absent from `EvaluationLog` — the table the dashboard reads.
"""

import pytest
from unittest.mock import patch

from rest_framework import status

from apps.evaluation.models import EvaluationLog
from apps.sdk.serializers import SDKImpressionBatchSerializer
from apps.sdk_keys.models import SDKKey

from conftest import (
    EnvironmentFactory,
    EnvironmentFlagFactory,
    FeatureFlagFactory,
    SDKKeyFactory,
)

ENDPOINT = "/api/v1/sdk/impressions/"


@pytest.fixture
def eager_celery():
    """Run dispatched tasks inline, inside the test transaction.

    Without this `.delay()` hands the batch to the real broker and the worker
    writes it to the *development* database, so the rows never appear in the
    test one and every ingest assertion silently checks nothing.
    """
    from config.celery import app

    previous = app.conf.task_always_eager
    app.conf.task_always_eager = True
    yield
    app.conf.task_always_eager = previous


def _post(api_client, sdk_key, impressions):
    return api_client.post(
        ENDPOINT, {"impressions": impressions}, format="json",
        HTTP_X_SDK_KEY=sdk_key._full_key,
    )


@pytest.fixture
def two_flags(project, environment):
    flags = []
    for key in ("dark-mode", "new-checkout"):
        flag = FeatureFlagFactory(project=project, key=key, is_enabled=True)
        EnvironmentFlagFactory(
            feature_flag=flag, environment=environment, is_enabled=True
        )
        flags.append(flag)
    return flags


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestIngest:
    def test_accepts_a_batch_and_writes_a_row_each(
        self, api_client, sdk_key, two_flags, eager_celery
    ):
        response = _post(api_client, sdk_key, [
            {"flag_key": "dark-mode", "result": True, "user_context": {"user_id": "u1"}},
            {"flag_key": "new-checkout", "result": False, "user_context": {"user_id": "u2"}},
        ])

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.data["accepted"] == 2
        assert EvaluationLog.objects.count() == 2

    def test_each_row_keeps_its_own_user_context(
        self, api_client, sdk_key, two_flags, eager_celery
    ):
        """The reason context is per impression: a flush spans many users."""
        _post(api_client, sdk_key, [
            {"flag_key": "dark-mode", "result": True, "user_context": {"user_id": "u1"}},
            {"flag_key": "dark-mode", "result": False, "user_context": {"user_id": "u2"}},
        ])

        contexts = sorted(
            log.context_data["user_id"] for log in EvaluationLog.objects.all()
        )
        assert contexts == ["u1", "u2"]

    def test_results_of_every_type_round_trip(
        self, api_client, sdk_key, two_flags, eager_celery
    ):
        _post(api_client, sdk_key, [
            {"flag_key": "dark-mode", "result": True},
            {"flag_key": "dark-mode", "result": "variant-b"},
            {"flag_key": "dark-mode", "result": 42},
            {"flag_key": "dark-mode", "result": {"theme": "dark"}},
        ])

        results = [log.result for log in EvaluationLog.objects.all()]
        assert True in results
        assert "variant-b" in results
        assert 42 in results
        assert {"theme": "dark"} in results

    def test_rows_are_visible_through_the_dashboard_log_api(
        self, api_client, auth_client, sdk_key, two_flags, eager_celery
    ):
        """The whole point — locally-evaluated flags reach the dashboard."""
        _post(api_client, sdk_key, [
            {"flag_key": "dark-mode", "result": True, "user_context": {"user_id": "u1"}},
        ])

        logs = auth_client.get("/api/v1/evaluation/logs/")

        assert logs.status_code == status.HTTP_200_OK
        assert any(row["flag_key"] == "dark-mode" for row in logs.data["results"])

    def test_an_empty_batch_is_accepted_and_queues_nothing(
        self, api_client, sdk_key, two_flags
    ):
        """An SDK with nothing to flush must not have to special-case it."""
        with patch("apps.evaluation.services.log_evaluations.delay") as delay:
            response = _post(api_client, sdk_key, [])

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.data["accepted"] == 0
        assert not delay.called

    def test_the_whole_batch_is_one_task(self, api_client, sdk_key, two_flags):
        """N tasks for one request is what this endpoint replaces."""
        with patch("apps.evaluation.services.log_evaluations.delay") as delay:
            _post(api_client, sdk_key, [
                {"flag_key": "dark-mode", "result": True} for _ in range(50)
            ])

        delay.assert_called_once()
        assert len(delay.call_args.kwargs["evaluations"]) == 50


# ---------------------------------------------------------------------------
# Unknown flags
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestUnknownFlags:
    def test_an_unknown_key_is_dropped_not_an_error(
        self, api_client, sdk_key, two_flags, eager_celery
    ):
        """One stale key must not reject every future flush."""
        response = _post(api_client, sdk_key, [
            {"flag_key": "dark-mode", "result": True},
            {"flag_key": "deleted-long-ago", "result": True},
        ])

        assert response.status_code == status.HTTP_202_ACCEPTED
        assert response.data["accepted"] == 1
        assert response.data["dropped"] == ["deleted-long-ago"]
        assert EvaluationLog.objects.count() == 1

    def test_dropped_keys_are_reported_once_each(
        self, api_client, sdk_key, two_flags
    ):
        response = _post(api_client, sdk_key, [
            {"flag_key": "ghost", "result": True},
            {"flag_key": "ghost", "result": False},
        ])

        assert response.data["dropped"] == ["ghost"]

    def test_an_archived_flag_is_dropped(
        self, api_client, sdk_key, two_flags, eager_celery
    ):
        two_flags[0].is_archived = True
        two_flags[0].save(update_fields=["is_archived"])

        response = _post(api_client, sdk_key, [
            {"flag_key": "dark-mode", "result": True},
        ])

        assert response.data["dropped"] == ["dark-mode"]
        assert EvaluationLog.objects.count() == 0

    def test_a_flag_from_another_environment_is_dropped(
        self, api_client, project, environment, sdk_key, two_flags
    ):
        """The key's environment scopes the ingest, as it does every SDK call."""
        staging = EnvironmentFactory(project=project, name="staging")
        other = FeatureFlagFactory(project=project, key="staging-only", is_enabled=True)
        EnvironmentFlagFactory(
            feature_flag=other, environment=staging, is_enabled=True
        )

        response = _post(api_client, sdk_key, [
            {"flag_key": "staging-only", "result": True},
        ])

        assert response.data["dropped"] == ["staging-only"]

    def test_a_flag_from_another_project_is_dropped(
        self, api_client, other_user, sdk_key, two_flags, eager_celery
    ):
        from conftest import personal_project_for

        foreign_project = personal_project_for(other_user)
        foreign = FeatureFlagFactory(
            project=foreign_project, key="foreign", is_enabled=True
        )
        foreign_env = EnvironmentFactory(project=foreign_project)
        EnvironmentFlagFactory(
            feature_flag=foreign, environment=foreign_env, is_enabled=True
        )

        response = _post(api_client, sdk_key, [{"flag_key": "foreign", "result": True}])

        assert response.data["dropped"] == ["foreign"]
        assert EvaluationLog.objects.count() == 0


# ---------------------------------------------------------------------------
# Limits and auth
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestLimitsAndAuth:
    def test_a_batch_over_the_cap_is_rejected(self, api_client, sdk_key, two_flags):
        """Unbounded, one request could queue an arbitrarily large task."""
        oversized = [
            {"flag_key": "dark-mode", "result": True}
            for _ in range(SDKImpressionBatchSerializer.MAX_BATCH + 1)
        ]

        response = _post(api_client, sdk_key, oversized)

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "impressions" in response.data

    def test_a_batch_at_the_cap_is_accepted(self, api_client, sdk_key, two_flags):
        at_cap = [
            {"flag_key": "dark-mode", "result": True}
            for _ in range(SDKImpressionBatchSerializer.MAX_BATCH)
        ]

        with patch("apps.evaluation.services.log_evaluations.delay"):
            response = _post(api_client, sdk_key, at_cap)

        assert response.status_code == status.HTTP_202_ACCEPTED

    def test_a_missing_flag_key_is_a_400(self, api_client, sdk_key, two_flags):
        response = _post(api_client, sdk_key, [{"result": True}])

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_client_keys_are_accepted(self, api_client, environment, two_flags):
        """A browser SDK reads flags the server has no record of serving either."""
        client_key = SDKKeyFactory(
            environment=environment, key_type=SDKKey.KeyType.CLIENT
        )

        with patch("apps.evaluation.services.log_evaluations.delay"):
            response = _post(api_client, client_key, [
                {"flag_key": "dark-mode", "result": True},
            ])

        assert response.status_code == status.HTTP_202_ACCEPTED

    def test_missing_key_is_401(self, api_client, two_flags):
        response = api_client.post(ENDPOINT, {"impressions": []}, format="json")

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_revoked_key_is_401(self, api_client, sdk_key, two_flags):
        sdk_key.is_active = False
        sdk_key.save(update_fields=["is_active"])

        response = _post(api_client, sdk_key, [{"flag_key": "dark-mode", "result": True}])

        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_resolution_costs_one_query_for_the_whole_batch(
        self, api_client, sdk_key, two_flags, django_assert_num_queries
    ):
        """A lookup per impression is what makes batching pointless."""
        batch = [
            {"flag_key": "dark-mode", "result": True} for _ in range(100)
        ] + [
            {"flag_key": "new-checkout", "result": False} for _ in range(100)
        ]

        with patch("apps.evaluation.services.log_evaluations.delay"):
            # SDK key lookup, its last_used_at update, and one key resolution.
            with django_assert_num_queries(3):
                response = _post(api_client, sdk_key, batch)

        assert response.data["accepted"] == 200
