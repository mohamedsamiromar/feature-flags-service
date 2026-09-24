"""
A flag or rule may only point at its OWN flag's variations.

`off_variation`, `fallthrough_variation`, and `serve_variation` are writable
foreign keys, and a ModelSerializer resolves them against the whole Variation
table. Whatever they point at is served verbatim by the SDK, so a missing
ownership check is a cross-tenant read: point your own flag at another
tenant's variation id, evaluate it with your own SDK key, read their value.

Also pins that a rule cannot be moved to a different flag — the write check
only ever saw the destination, so a viewer could strip rules off a flag they
could not edit by re-parenting them into a project of their own.
"""

from unittest.mock import patch

import pytest

from apps.flags.models import FeatureFlag, Variation
from apps.organizations.models import Role
from apps.rules.models import Rule
from conftest import (
    EnvironmentFactory,
    EnvironmentFlagFactory,
    FeatureFlagFactory,
    MembershipFactory,
    ProjectFactory,
    SDKKeyFactory,
    VariationFactory,
)

RULES = "/api/v1/rules/"


@pytest.fixture
def foreign_variation(db):
    """A variation in a project the authenticated user has no access to."""
    return VariationFactory(
        flag=FeatureFlagFactory(project=ProjectFactory()),
        value_type=Variation.ValueType.STRING,
        value="tenant-b-secret",
    )


@pytest.mark.django_db
class TestFlagCreateRejectsForeignVariation:
    @pytest.mark.parametrize("field", ["off_variation", "fallthrough_variation"])
    def test_rejected(self, auth_client, base, foreign_variation, field):
        resp = auth_client.post(
            f"{base}/",
            {
                "name": "Probe",
                "key": "probe",
                "flag_type": FeatureFlag.FlagType.MULTIVARIATE,
                field: foreign_variation.id,
            },
            format="json",
        )
        assert resp.status_code == 400
        assert not FeatureFlag.objects.filter(key="probe").exists()

    def test_foreign_value_never_reaches_the_sdk(
        self, auth_client, api_client, base, project, foreign_variation
    ):
        """The exploit end to end: create, then evaluate with your own key."""
        auth_client.post(
            f"{base}/",
            {
                "name": "Probe",
                "key": "probe",
                "flag_type": FeatureFlag.FlagType.MULTIVARIATE,
                "off_variation": foreign_variation.id,
            },
            format="json",
        )
        environment = EnvironmentFactory(project=project)
        probe = FeatureFlag.objects.filter(key="probe", project=project).first()
        if probe is not None:
            EnvironmentFlagFactory(
                feature_flag=probe, environment=environment, is_enabled=False
            )
        key = SDKKeyFactory(environment=environment)

        with patch("apps.sdk.views.log_evaluation.delay"):
            resp = api_client.post(
                "/api/v1/sdk/evaluate/",
                {"flag_key": "probe", "user_context": {"user_id": "u1"}},
                format="json",
                HTTP_X_SDK_KEY=key._full_key,
            )
        # Either the create was refused (404 here) or the flag serves no foreign
        # value. Compare parsed JSON: `str(resp.content)` escapes quotes and once
        # let this assertion pass while the secret was in the body.
        assert resp.status_code == 404 or resp.json().get("result") != "tenant-b-secret"


@pytest.mark.django_db
class TestRuleServeVariationMustBelongToFlag:
    def test_create_rejects_foreign_variation(self, auth_client, flag, foreign_variation):
        resp = auth_client.post(
            RULES,
            {
                "flag": flag.id,
                "attribute": "plan",
                "operator": "eq",
                "value": "pro",
                "serve_variation": foreign_variation.id,
            },
            format="json",
        )
        assert resp.status_code == 400
        assert not Rule.objects.filter(flag=flag).exists()

    def test_update_rejects_foreign_variation(self, auth_client, flag, foreign_variation):
        rule = Rule.objects.create(flag=flag, attribute="plan", operator="eq", value="pro")
        resp = auth_client.patch(
            f"{RULES}{rule.id}/",
            {"serve_variation": foreign_variation.id},
            format="json",
        )
        assert resp.status_code == 400
        rule.refresh_from_db()
        assert rule.serve_variation_id is None

    def test_own_variation_still_accepted(self, auth_client, flag):
        own = VariationFactory(flag=flag)
        resp = auth_client.post(
            RULES,
            {
                "flag": flag.id,
                "attribute": "plan",
                "operator": "eq",
                "value": "pro",
                "serve_variation": own.id,
            },
            format="json",
        )
        assert resp.status_code == 201


@pytest.mark.django_db
class TestRuleCannotChangeFlag:
    def test_viewer_cannot_move_rule_out_of_flag(self, auth_client, user, flag):
        """A viewer in the victim org re-parents a rule into their own project."""
        victim_project = ProjectFactory()
        MembershipFactory(
            organization=victim_project.organization, user=user, role=Role.VIEWER
        )
        victim_flag = FeatureFlagFactory(project=victim_project)
        rule = Rule.objects.create(
            flag=victim_flag, attribute="plan", operator="eq", value="pro"
        )

        resp = auth_client.patch(f"{RULES}{rule.id}/", {"flag": flag.id}, format="json")

        assert resp.status_code == 403
        rule.refresh_from_db()
        assert rule.flag_id == victim_flag.id

    def test_move_between_own_flags_rejected(self, auth_client, flag, project):
        other = FeatureFlagFactory(project=project)
        rule = Rule.objects.create(flag=flag, attribute="plan", operator="eq", value="pro")

        resp = auth_client.patch(f"{RULES}{rule.id}/", {"flag": other.id}, format="json")

        assert resp.status_code == 400
        rule.refresh_from_db()
        assert rule.flag_id == flag.id

    def test_put_with_same_flag_still_works(self, auth_client, flag):
        rule = Rule.objects.create(flag=flag, attribute="plan", operator="eq", value="pro")
        resp = auth_client.put(
            f"{RULES}{rule.id}/",
            {"flag": flag.id, "attribute": "plan", "operator": "eq", "value": "team"},
            format="json",
        )
        assert resp.status_code == 200
