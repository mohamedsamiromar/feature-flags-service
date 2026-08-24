"""
Prerequisite flags — a flag evaluates normally only while its prerequisite is
serving a required variation for the same user.

The two things worth being careful about are cycles (rejected at write time,
capped at evaluation time) and failing closed: a dependent that cannot confirm
its gate must stay off rather than guess its way open.
"""

import pytest
from conftest import EnvironmentFlagFactory, FeatureFlagFactory, UserFactory, VariationFactory

from apps.core.errors import APIError
from apps.evaluation.services import FlagEvaluationService
from apps.flags.models import FlagPrerequisite
from apps.flags.services import FlagService

_service = FlagService()


def _wire(flag, environment, enabled=True, rollout=100):
    """Give a flag on/off variations and a per-env state."""
    on = VariationFactory(flag=flag, name="on", value_type="boolean", value=True)
    off = VariationFactory(flag=flag, name="off", value_type="boolean", value=False)
    flag.fallthrough_variation, flag.off_variation = on, off
    flag.save(update_fields=["fallthrough_variation", "off_variation"])
    EnvironmentFlagFactory(
        feature_flag=flag, environment=environment,
        is_enabled=enabled, rollout_percentage=rollout,
    )
    return on, off


@pytest.fixture
def gated(user, project, environment, flag):
    """`flag` (dependent) gated behind `parent` serving its `on` variation."""
    parent = FeatureFlagFactory(project=project, key="parent-flag")
    parent_on, parent_off = _wire(parent, environment)
    _wire(flag, environment)
    _service.add_prerequisite(
        project_key=project.key, key=flag.key, user=user,
        prerequisite_key=parent.key, variation_id=parent_on.id,
    )
    return flag, parent, parent_on, parent_off


def _evaluate(flag, environment, user_id="alice", **attrs):
    return FlagEvaluationService().evaluate(
        flag_key=flag.key, project_id=flag.project_id,
        user_context={"user_id": user_id, **attrs}, env_id=environment.id,
    ).result


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestPrerequisiteGate:
    def test_met_prerequisite_lets_the_flag_through(self, gated, environment):
        flag, *_ = gated
        assert _evaluate(flag, environment) is True

    def test_prerequisite_off_closes_the_gate(self, user, project, gated, environment):
        flag, parent, *_ = gated
        _service.toggle_environment(
            project_key=project.key, key=parent.key, user=user, env_name=environment.name
        )
        assert _evaluate(parent, environment) is False
        assert _evaluate(flag, environment) is False

    def test_prerequisite_serving_another_variation_closes_the_gate(
        self, user, project, environment, flag
    ):
        parent = FeatureFlagFactory(project=project, key="parent-flag")
        parent_on, parent_off = _wire(parent, environment)
        _wire(flag, environment)
        # Require the OFF variation while the parent is serving ON.
        _service.add_prerequisite(
            project_key=project.key, key=flag.key, user=user,
            prerequisite_key=parent.key, variation_id=parent_off.id,
        )
        assert _evaluate(parent, environment) is True
        assert _evaluate(flag, environment) is False

    def test_gate_applies_before_individual_targeting(self, user, project, gated, environment):
        """An explicitly targeted user still does not get a gated-off flag."""
        flag, parent, parent_on, _ = gated
        _service.toggle_environment(
            project_key=project.key, key=parent.key, user=user, env_name=environment.name
        )
        target_variation = flag.variations.get(name="on")
        _service.set_target(
            project_key=project.key, key=flag.key, user=user,
            user_key="alice", variation_id=target_variation.id,
        )
        assert _evaluate(flag, environment, user_id="alice") is False

    def test_a_flag_with_no_prerequisites_is_unaffected(self, environment, flag):
        _wire(flag, environment)
        assert _evaluate(flag, environment) is True

    def test_chain_of_three_requires_the_whole_chain(self, user, project, environment):
        a = FeatureFlagFactory(project=project, key="chain-a")
        b = FeatureFlagFactory(project=project, key="chain-b")
        c = FeatureFlagFactory(project=project, key="chain-c")
        a_on, _ = _wire(a, environment)
        b_on, _ = _wire(b, environment)
        _wire(c, environment)
        # c requires b, b requires a
        _service.add_prerequisite(
            project_key=project.key, key=b.key, user=user,
            prerequisite_key=a.key, variation_id=a_on.id,
        )
        _service.add_prerequisite(
            project_key=project.key, key=c.key, user=user,
            prerequisite_key=b.key, variation_id=b_on.id,
        )
        assert _evaluate(c, environment) is True

        # Break the root of the chain; the far end must close.
        _service.toggle_environment(
            project_key=project.key, key=a.key, user=user, env_name=environment.name
        )
        assert _evaluate(c, environment) is False


# ---------------------------------------------------------------------------
# Cycles
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCycleRejection:
    def test_flag_cannot_require_itself(self, user, project, environment, flag):
        on, _ = _wire(flag, environment)
        with pytest.raises(APIError):
            _service.add_prerequisite(
                project_key=project.key, key=flag.key, user=user,
                prerequisite_key=flag.key, variation_id=on.id,
            )

    def test_direct_two_flag_cycle_is_rejected(self, user, project, environment, flag):
        parent = FeatureFlagFactory(project=project, key="parent-flag")
        parent_on, _ = _wire(parent, environment)
        flag_on, _ = _wire(flag, environment)
        _service.add_prerequisite(
            project_key=project.key, key=flag.key, user=user,
            prerequisite_key=parent.key, variation_id=parent_on.id,
        )
        # parent -> flag would close the loop
        with pytest.raises(APIError):
            _service.add_prerequisite(
                project_key=project.key, key=parent.key, user=user,
                prerequisite_key=flag.key, variation_id=flag_on.id,
            )

    def test_indirect_three_flag_cycle_is_rejected(self, user, project, environment):
        a = FeatureFlagFactory(project=project, key="cyc-a")
        b = FeatureFlagFactory(project=project, key="cyc-b")
        c = FeatureFlagFactory(project=project, key="cyc-c")
        a_on, _ = _wire(a, environment)
        b_on, _ = _wire(b, environment)
        c_on, _ = _wire(c, environment)
        _service.add_prerequisite(
            project_key=project.key, key=b.key, user=user,
            prerequisite_key=a.key, variation_id=a_on.id,
        )
        _service.add_prerequisite(
            project_key=project.key, key=c.key, user=user,
            prerequisite_key=b.key, variation_id=b_on.id,
        )
        # a -> c closes a -> c -> b -> a
        with pytest.raises(APIError):
            _service.add_prerequisite(
                project_key=project.key, key=a.key, user=user,
                prerequisite_key=c.key, variation_id=c_on.id,
            )

    def test_a_diamond_is_not_a_cycle(self, user, project, environment):
        """Two flags may share a prerequisite; only loops are forbidden."""
        root = FeatureFlagFactory(project=project, key="dia-root")
        left = FeatureFlagFactory(project=project, key="dia-left")
        right = FeatureFlagFactory(project=project, key="dia-right")
        root_on, _ = _wire(root, environment)
        _wire(left, environment)
        _wire(right, environment)
        _service.add_prerequisite(
            project_key=project.key, key=left.key, user=user,
            prerequisite_key=root.key, variation_id=root_on.id,
        )
        _service.add_prerequisite(
            project_key=project.key, key=right.key, user=user,
            prerequisite_key=root.key, variation_id=root_on.id,
        )
        assert _evaluate(left, environment) is True
        assert _evaluate(right, environment) is True

    def test_evaluation_survives_a_cycle_written_straight_to_the_db(
        self, user, project, environment, flag
    ):
        """The service rejects cycles, so this can only come from a direct DB
        write or a bad migration. Evaluation must fail closed, not hang."""
        parent = FeatureFlagFactory(project=project, key="parent-flag")
        parent_on, _ = _wire(parent, environment)
        flag_on, _ = _wire(flag, environment)
        FlagPrerequisite.objects.create(
            flag=flag, prerequisite_flag=parent, required_variation=parent_on
        )
        FlagPrerequisite.objects.create(
            flag=parent, prerequisite_flag=flag, required_variation=flag_on
        )
        assert _evaluate(flag, environment) is False
        assert _evaluate(parent, environment) is False


# ---------------------------------------------------------------------------
# Failing closed
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestFailsClosed:
    def test_prerequisite_missing_from_this_environment_closes_the_gate(
        self, user, project, environment, flag
    ):
        """The parent has no EnvironmentFlag row here, so it cannot be resolved."""
        parent = FeatureFlagFactory(project=project, key="parent-flag")
        parent_on = VariationFactory(flag=parent, name="on", value_type="boolean", value=True)
        VariationFactory(flag=parent, name="off", value_type="boolean", value=False)
        _wire(flag, environment)
        _service.add_prerequisite(
            project_key=project.key, key=flag.key, user=user,
            prerequisite_key=parent.key, variation_id=parent_on.id,
        )
        assert _evaluate(flag, environment) is False

    def test_gate_closes_when_the_prerequisite_is_archived(
        self, user, project, gated, environment
    ):
        """Archiving a gating flag is refused by the service, so this state can
        only arise from a direct DB write. The dependent must still fail closed
        rather than raise out of the SDK endpoint."""
        flag, parent, *_ = gated
        assert _evaluate(flag, environment) is True

        parent.is_archived = True
        parent.save(update_fields=["is_archived"])
        _service.invalidate_flag_caches(flag)
        _service.invalidate_flag_caches(parent)

        assert _evaluate(flag, environment) is False


# ---------------------------------------------------------------------------
# Write-side guards
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestPrerequisiteWriteGuards:
    def test_variation_must_belong_to_the_prerequisite(self, user, project, environment, flag):
        parent = FeatureFlagFactory(project=project, key="parent-flag")
        _wire(parent, environment)
        flag_on, _ = _wire(flag, environment)
        # Passing the DEPENDENT's variation would create an unsatisfiable gate.
        with pytest.raises(APIError):
            _service.add_prerequisite(
                project_key=project.key, key=flag.key, user=user,
                prerequisite_key=parent.key, variation_id=flag_on.id,
            )

    def test_prerequisite_from_another_project_is_invisible(self, user, project, environment, flag):
        foreign = FeatureFlagFactory()
        foreign_on = VariationFactory(flag=foreign, name="on", value_type="boolean", value=True)
        _wire(flag, environment)
        with pytest.raises(APIError):
            _service.add_prerequisite(
                project_key=project.key, key=flag.key, user=user,
                prerequisite_key=foreign.key, variation_id=foreign_on.id,
            )

    def test_cannot_archive_a_flag_that_gates_another(self, user, project, gated):
        flag, parent, *_ = gated
        with pytest.raises(APIError):
            _service.archive_flag(project_key=project.key, key=parent.key, user=user)

    def test_cannot_delete_a_flag_that_gates_another(self, user, project, gated):
        flag, parent, *_ = gated
        with pytest.raises(APIError):
            _service.delete_flag(project_key=project.key, key=parent.key, user=user)

    def test_archive_allowed_once_the_gate_is_removed(self, user, project, gated):
        flag, parent, *_ = gated
        _service.remove_prerequisite(
            project_key=project.key, key=flag.key, user=user, prerequisite_key=parent.key
        )
        archived = _service.archive_flag(project_key=project.key, key=parent.key, user=user)
        assert archived.is_archived is True

    def test_non_member_cannot_add_a_prerequisite(self, project, environment, flag):
        parent = FeatureFlagFactory(project=project, key="parent-flag")
        parent_on, _ = _wire(parent, environment)
        _wire(flag, environment)
        with pytest.raises(APIError):
            _service.add_prerequisite(
                project_key=project.key, key=flag.key, user=UserFactory(),
                prerequisite_key=parent.key, variation_id=parent_on.id,
            )


# ---------------------------------------------------------------------------
# Cache + API
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestPrerequisiteCacheInvalidation:
    def test_adding_a_gate_takes_effect_immediately(self, user, project, environment, flag):
        parent = FeatureFlagFactory(project=project, key="parent-flag")
        parent_on, parent_off = _wire(parent, environment)
        _wire(flag, environment)
        assert _evaluate(flag, environment) is True  # prime the cache

        _service.add_prerequisite(
            project_key=project.key, key=flag.key, user=user,
            prerequisite_key=parent.key, variation_id=parent_off.id,
        )
        assert _evaluate(flag, environment) is False

    def test_removing_a_gate_takes_effect_immediately(self, user, project, gated, environment):
        flag, parent, *_ = gated
        _service.toggle_environment(
            project_key=project.key, key=parent.key, user=user, env_name=environment.name
        )
        assert _evaluate(flag, environment) is False

        _service.remove_prerequisite(
            project_key=project.key, key=flag.key, user=user, prerequisite_key=parent.key
        )
        assert _evaluate(flag, environment) is True


@pytest.mark.django_db
class TestPrerequisiteAPI:
    def test_put_creates_and_returns_201(self, base, auth_client, project, environment, flag):
        parent = FeatureFlagFactory(project=project, key="parent-flag")
        parent_on, _ = _wire(parent, environment)
        _wire(flag, environment)
        resp = auth_client.put(
            f"{base}/{flag.key}/prerequisites/",
            {"prerequisite_key": parent.key, "variation_id": parent_on.id},
            format="json",
        )
        assert resp.status_code == 201
        assert resp.json()["prerequisite_key"] == parent.key
        assert resp.json()["variation_name"] == "on"

    def test_put_twice_updates_and_returns_200(self, base, auth_client, project, environment, flag):
        parent = FeatureFlagFactory(project=project, key="parent-flag")
        parent_on, parent_off = _wire(parent, environment)
        _wire(flag, environment)
        auth_client.put(
            f"{base}/{flag.key}/prerequisites/",
            {"prerequisite_key": parent.key, "variation_id": parent_on.id}, format="json",
        )
        resp = auth_client.put(
            f"{base}/{flag.key}/prerequisites/",
            {"prerequisite_key": parent.key, "variation_id": parent_off.id}, format="json",
        )
        assert resp.status_code == 200
        assert resp.json()["variation_name"] == "off"

    def test_list_returns_gates(self, base, auth_client, gated):
        flag, parent, *_ = gated
        resp = auth_client.get(f"{base}/{flag.key}/prerequisites/")
        assert resp.status_code == 200
        assert [r["prerequisite_key"] for r in resp.json()] == [parent.key]

    def test_delete_returns_204(self, base, auth_client, gated):
        flag, parent, *_ = gated
        resp = auth_client.delete(f"{base}/{flag.key}/prerequisites/{parent.key}/")
        assert resp.status_code == 204
        assert not FlagPrerequisite.objects.filter(flag=flag).exists()

    def test_cycle_returns_409(self, base, auth_client, gated, environment):
        flag, parent, _parent_on, _ = gated
        flag_on = flag.variations.get(name="on")
        resp = auth_client.put(
            f"{base}/{parent.key}/prerequisites/",
            {"prerequisite_key": flag.key, "variation_id": flag_on.id}, format="json",
        )
        assert resp.status_code == 409

    def test_archiving_a_gating_flag_returns_409(self, base, auth_client, gated):
        _flag, parent, *_ = gated
        resp = auth_client.post(f"{base}/{parent.key}/archive/")
        assert resp.status_code == 409

    def test_unknown_prerequisite_delete_returns_404(self, base, auth_client, flag):
        resp = auth_client.delete(f"{base}/{flag.key}/prerequisites/nope/")
        assert resp.status_code == 404
