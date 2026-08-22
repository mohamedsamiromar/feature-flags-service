"""
Load-bearing assumptions behind segment evaluation.

None of these are features — they are properties the design depends on without
saying so. Each was verified by hand during review; they live here so a future
change that breaks one fails loudly instead of drifting.

The sharpest of them is the URL regex: DRF's default detail pattern (`[^/.]+`)
stops at the first dot, so it silently truncates every email-shaped user key.
"""

import pytest
from django.core.cache import cache

from conftest import EnvironmentFlagFactory, VariationFactory
from apps.evaluation.services import FlagEvaluationService
from apps.rules.models import Operator, Rule
from apps.segments.queries import SegmentQuery
from apps.segments.services import SegmentService

_service = SegmentService()


@pytest.fixture
def segment(user, project):
    return _service.create_segment(
        project_key=project.key, user=user, key="beta", name="Beta"
    )


# ---------------------------------------------------------------------------
# 1. Operator identity across the cache boundary
# ---------------------------------------------------------------------------

class TestOperatorSetMembership:
    """`Operator.segment_operators()` returns enum members, but every runtime
    check compares it against a RAW STRING read back out of Redis. Django's
    TextChoices hash by value, which is the only reason that works — and three
    separate guards depend on it."""

    def test_raw_strings_match_enum_members(self):
        ops = Operator.segment_operators()
        assert "in_segment" in ops
        assert "not_in_segment" in ops

    def test_non_segment_operators_do_not_match(self):
        ops = Operator.segment_operators()
        for value in ("eq", "neq", "contains", "in", "not_in", "gt", "lt"):
            assert value not in ops, f"{value} must not be treated as a segment operator"

    def test_every_operator_is_classified_exactly_one_way(self):
        """A new operator added without thought must not land in limbo."""
        segment_ops = Operator.segment_operators()
        for op in Operator:
            assert (op in segment_ops) == op.value.endswith("_segment")


# ---------------------------------------------------------------------------
# 2. The cached payload survives a real Redis round trip
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCachedPayloadRoundTrip:
    """`evaluation_payload` puts Python `set` objects in the cached flag data.
    Redis stores bytes — this only works because the backend pickles. A backend
    or serializer change (e.g. switching to JSON) would silently turn the sets
    into lists, or fail outright."""

    def test_sets_survive_the_cache(self, user, project, segment):
        _service.set_target(
            project_key=project.key, key=segment.key, user=user,
            user_key="alice", excluded=False,
        )
        _service.set_target(
            project_key=project.key, key=segment.key, user=user,
            user_key="bob", excluded=True,
        )
        payload = SegmentQuery.evaluation_payload(project.id, [segment.key])

        cache.set("invariant-probe", payload, 30)
        restored = cache.get("invariant-probe")
        cache.delete("invariant-probe")

        assert restored == payload
        assert isinstance(restored[segment.key]["included"], set)
        assert restored[segment.key]["included"] == {"alice"}
        assert restored[segment.key]["excluded"] == {"bob"}

    def test_evaluation_is_identical_cold_and_warm(self, user, project, segment, flag, environment):
        """The real guarantee: a warm cache must answer exactly as a cold one."""
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
        _service.set_target(
            project_key=project.key, key=segment.key, user=user,
            user_key="alice", excluded=False,
        )

        def evaluate(user_id):
            return FlagEvaluationService().evaluate(
                flag_key=flag.key, project_id=flag.project_id,
                user_context={"user_id": user_id}, env_id=environment.id,
            ).result

        cold_member, cold_stranger = evaluate("alice"), evaluate("bob")
        warm_member, warm_stranger = evaluate("alice"), evaluate("bob")

        assert (cold_member, cold_stranger) == (True, False)
        assert (warm_member, warm_stranger) == (cold_member, cold_stranger)


# ---------------------------------------------------------------------------
# 3. User keys are real-world identifiers, not slugs
# ---------------------------------------------------------------------------

# Emails are the most common user key, and DRF's default detail regex
# (`[^/.]+`) stops at the first dot — which would truncate every one of these.
REAL_WORLD_USER_KEYS = [
    "a.b@example.com",
    "user+tag@example.co.uk",
    "with.dots",
    "UPPER-Case_123",
]


@pytest.mark.django_db
class TestSegmentTargetUserKeysInUrls:
    @pytest.fixture
    def seg_base(self, project):
        return f"/api/v1/projects/{project.key}/segments"

    @pytest.mark.parametrize("user_key", REAL_WORLD_USER_KEYS)
    def test_target_round_trips_through_the_url(self, seg_base, auth_client, segment, user_key):
        created = auth_client.put(
            f"{seg_base}/{segment.key}/targets/",
            {"user_key": user_key, "excluded": False}, format="json",
        )
        assert created.status_code == 201
        assert created.json()["user_key"] == user_key

        listed = auth_client.get(f"{seg_base}/{segment.key}/targets/")
        assert [row["user_key"] for row in listed.json()] == [user_key]

        removed = auth_client.delete(f"{seg_base}/{segment.key}/targets/{user_key}/")
        assert removed.status_code == 204, f"could not delete target keyed {user_key!r}"


@pytest.mark.django_db
class TestFlagTargetUserKeysInUrls:
    @pytest.mark.parametrize("user_key", REAL_WORLD_USER_KEYS)
    def test_target_round_trips_through_the_url(self, base, auth_client, flag, user_key):
        variation = VariationFactory(flag=flag, name="on", value_type="boolean", value=True)
        created = auth_client.put(
            f"{base}/{flag.key}/targets/",
            {"user_key": user_key, "variation": variation.id}, format="json",
        )
        assert created.status_code == 201
        assert created.json()["user_key"] == user_key

        removed = auth_client.delete(f"{base}/{flag.key}/targets/{user_key}/")
        assert removed.status_code == 204, f"could not delete target keyed {user_key!r}"

    @pytest.mark.parametrize("user_key", REAL_WORLD_USER_KEYS)
    def test_targeted_user_is_matched_at_evaluation(
        self, user, flag, environment, user_key
    ):
        """The key must survive storage and match the SDK's user_id verbatim."""
        from apps.flags.services import FlagService

        on = VariationFactory(flag=flag, name="on", value_type="boolean", value=True)
        off = VariationFactory(flag=flag, name="off", value_type="boolean", value=False)
        flag.fallthrough_variation, flag.off_variation = on, off
        flag.save(update_fields=["fallthrough_variation", "off_variation"])
        EnvironmentFlagFactory(
            feature_flag=flag, environment=environment,
            is_enabled=True, rollout_percentage=0,
        )
        FlagService().set_target(
            project_key=flag.project.key, key=flag.key, user=user,
            user_key=user_key, variation_id=on.id,
        )
        result = FlagEvaluationService().evaluate(
            flag_key=flag.key, project_id=flag.project_id,
            user_context={"user_id": user_key}, env_id=environment.id,
        ).result
        assert result is True
