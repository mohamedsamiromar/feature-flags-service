"""Audit coverage for rules, SDK keys, and organizations.

Flag, variation, environment, segment, target, and prerequisite mutations were
already audited and are covered by their own apps' tests. These three were the
gap: rules decide *who* gets a flag, SDK keys are long-lived production
credentials, and membership is the access-control boundary itself. A trail that
records the flag but not the rule that changed its rollout answers the wrong
half of "what happened at 03:00".
"""

import pytest
from rest_framework import status

from apps.audit.models import AuditLog
from apps.audit.services import AuditService
from apps.organizations.models import Membership, Organization, Project, Role
from apps.rules.models import Operator, Rule
from apps.sdk_keys.models import SDKKey

from conftest import UserFactory, join_via_invitation


def entries(entity_type, action=None):
    qs = AuditLog.objects.filter(entity_type=entity_type)
    return qs.filter(action=action) if action else qs


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestRuleAudit:
    def test_create_is_audited(self, auth_client, flag, user):
        response = auth_client.post(
            "/api/v1/rules/",
            {"flag": flag.id, "attribute": "plan", "operator": Operator.EQUALS, "value": "pro"},
            format="json",
        )
        assert response.status_code == status.HTTP_201_CREATED

        log = entries("rule", AuditService.CREATE).get()
        assert log.user == user
        assert log.entity_id == str(response.data["id"])
        assert log.old_value is None
        assert log.new_value["value"] == "pro"

    def test_update_records_both_sides(self, auth_client, flag):
        created = auth_client.post(
            "/api/v1/rules/",
            {"flag": flag.id, "attribute": "plan", "operator": Operator.EQUALS, "value": "pro"},
            format="json",
        )
        auth_client.patch(
            f"/api/v1/rules/{created.data['id']}/", {"value": "enterprise"}, format="json"
        )

        log = entries("rule", AuditService.UPDATE).get()
        assert log.old_value["value"] == "pro"
        assert log.new_value["value"] == "enterprise"

    def test_rollout_change_is_reconstructable(self, auth_client, flag):
        """The reason rules are audited: who widened the rollout, and from what."""
        created = auth_client.post(
            "/api/v1/rules/",
            {
                "flag": flag.id,
                "attribute": "plan",
                "operator": Operator.EQUALS,
                "value": "pro",
                "rollout_percentage": 10,
            },
            format="json",
        )
        auth_client.patch(
            f"/api/v1/rules/{created.data['id']}/",
            {"rollout_percentage": 100},
            format="json",
        )

        log = entries("rule", AuditService.UPDATE).get()
        assert log.old_value["rollout_percentage"] == 10
        assert log.new_value["rollout_percentage"] == 100

    def test_delete_keeps_the_entity_id(self, auth_client, flag):
        created = auth_client.post(
            "/api/v1/rules/",
            {"flag": flag.id, "attribute": "plan", "operator": Operator.EQUALS, "value": "pro"},
            format="json",
        )
        rule_id = created.data["id"]

        auth_client.delete(f"/api/v1/rules/{rule_id}/")

        log = entries("rule", AuditService.DELETE).get()
        assert log.entity_id == str(rule_id)
        assert log.old_value["value"] == "pro"
        assert not Rule.objects.filter(id=rule_id).exists()

    def test_rejected_write_is_not_audited(self, auth_client, flag):
        """A 400 changed nothing, so it must leave no entry."""
        response = auth_client.post(
            "/api/v1/rules/",
            {"flag": flag.id, "attribute": "age", "operator": Operator.GT, "value": "eighteen"},
            format="json",
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not entries("rule").exists()


# ---------------------------------------------------------------------------
# SDK keys
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestSDKKeyAudit:
    def _create(self, auth_client, environment):
        return auth_client.post(
            "/api/v1/sdk-keys/",
            {"name": "Prod Server", "key_type": SDKKey.KeyType.SERVER,
             "environment": environment.id},
            format="json",
        )

    def test_create_is_audited(self, auth_client, environment, user):
        response = self._create(auth_client, environment)
        assert response.status_code == status.HTTP_201_CREATED

        log = entries("sdkkey", AuditService.CREATE).get()
        assert log.user == user
        assert log.new_value["name"] == "Prod Server"

    def test_snapshot_never_carries_the_key_hash(self, auth_client, environment):
        """Hash-only storage only means something while the digest lives in one
        table. An audit entry is a second table with different access rules."""
        self._create(auth_client, environment)

        log = entries("sdkkey", AuditService.CREATE).get()
        assert "hashed_key" not in log.new_value

        key = SDKKey.objects.get(name="Prod Server")
        assert key.hashed_key not in str(log.new_value)

    def test_prefix_is_kept_so_the_key_is_identifiable(self, auth_client, environment):
        """The prefix is the non-secret half — without it the entry names no key."""
        self._create(auth_client, environment)

        log = entries("sdkkey", AuditService.CREATE).get()
        assert log.new_value["prefix"].startswith("sdk_srv_")

    def test_revoke_is_audited_as_revoke(self, auth_client, environment):
        created = self._create(auth_client, environment)

        auth_client.post(f"/api/v1/sdk-keys/{created.data['id']}/revoke/")

        log = entries("sdkkey", AuditService.REVOKE).get()
        assert log.old_value["is_active"] is True
        assert log.new_value["is_active"] is False

    def test_rotate_is_audited_as_rotate_plus_a_create(self, auth_client, environment):
        """Two events, deliberately: the old key was replaced, a new one issued."""
        created = self._create(auth_client, environment)
        old_id = created.data["id"]

        rotated = auth_client.post(f"/api/v1/sdk-keys/{old_id}/rotate/")

        rotate_log = entries("sdkkey", AuditService.ROTATE).get()
        assert rotate_log.entity_id == str(old_id)
        assert rotate_log.new_value["is_active"] is False

        create_logs = entries("sdkkey", AuditService.CREATE)
        assert create_logs.count() == 2
        assert create_logs.filter(entity_id=str(rotated.data["id"])).exists()

    def test_a_second_revoke_is_refused_and_not_audited(self, auth_client, environment):
        created = self._create(auth_client, environment)
        auth_client.post(f"/api/v1/sdk-keys/{created.data['id']}/revoke/")

        again = auth_client.post(f"/api/v1/sdk-keys/{created.data['id']}/revoke/")

        assert again.status_code == status.HTTP_409_CONFLICT
        assert entries("sdkkey", AuditService.REVOKE).count() == 1


# ---------------------------------------------------------------------------
# Organizations, memberships, projects
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestOrganizationAudit:
    def test_create_audits_the_org_and_the_owner_membership(self, auth_client, user):
        response = auth_client.post(
            "/api/v1/organizations/", {"name": "Acme"}, format="json"
        )
        assert response.status_code == status.HTTP_201_CREATED

        org_log = entries("organization", AuditService.CREATE).get()
        assert org_log.new_value["name"] == "Acme"

        membership_log = entries("membership", AuditService.CREATE).get()
        assert membership_log.new_value["role"] == Role.OWNER

    def test_delete_is_audited(self, auth_client):
        created = auth_client.post(
            "/api/v1/organizations/", {"name": "Acme"}, format="json"
        )
        slug = created.data["slug"]

        auth_client.delete(f"/api/v1/organizations/{slug}/")

        log = entries("organization", AuditService.DELETE).get()
        assert log.old_value["slug"] == slug
        assert not Organization.objects.filter(slug=slug).exists()

    def test_role_change_records_the_privilege_move(self, auth_client):
        created = auth_client.post(
            "/api/v1/organizations/", {"name": "Acme"}, format="json"
        )
        slug = created.data["slug"]
        newcomer = UserFactory()
        join_via_invitation(auth_client, slug, newcomer, Role.VIEWER)

        auth_client.patch(
            f"/api/v1/organizations/{slug}/members/{newcomer.id}/",
            {"role": Role.ADMIN},
            format="json",
        )

        log = entries("membership", AuditService.UPDATE).get()
        assert log.old_value["role"] == Role.VIEWER
        assert log.new_value["role"] == Role.ADMIN

    def test_member_removal_is_audited(self, auth_client):
        created = auth_client.post(
            "/api/v1/organizations/", {"name": "Acme"}, format="json"
        )
        slug = created.data["slug"]
        newcomer = UserFactory()
        join_via_invitation(auth_client, slug, newcomer, Role.MEMBER)

        auth_client.delete(f"/api/v1/organizations/{slug}/members/{newcomer.id}/")

        log = entries("membership", AuditService.DELETE).get()
        assert log.old_value["user"] == newcomer.id
        assert not Membership.objects.filter(user=newcomer).exists()

    def test_last_owner_refusal_is_not_audited(self, auth_client, user):
        """A 409 changed nothing, so the trail must not claim a role moved."""
        created = auth_client.post(
            "/api/v1/organizations/", {"name": "Acme"}, format="json"
        )
        slug = created.data["slug"]

        response = auth_client.patch(
            f"/api/v1/organizations/{slug}/members/{user.id}/",
            {"role": Role.VIEWER},
            format="json",
        )

        assert response.status_code == status.HTTP_409_CONFLICT
        assert not entries("membership", AuditService.UPDATE).exists()

    def test_project_create_and_delete_are_audited(self, auth_client):
        org = auth_client.post(
            "/api/v1/organizations/", {"name": "Acme"}, format="json"
        )
        created = auth_client.post(
            "/api/v1/projects/",
            {"organization": org.data["slug"], "name": "Web"},
            format="json",
        )
        assert created.status_code == status.HTTP_201_CREATED
        key = created.data["key"]

        assert entries("project", AuditService.CREATE).filter(
            new_value__key=key
        ).exists()

        auth_client.delete(f"/api/v1/projects/{key}/")

        log = entries("project", AuditService.DELETE).get()
        assert log.old_value["key"] == key
        assert not Project.objects.filter(key=key).exists()


# ---------------------------------------------------------------------------
# The delete helper itself
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestLogDelete:
    def test_restores_the_pk_django_clears_on_delete(self, user):
        """`Model.delete()` sets pk to None; logging afterwards would record
        entity_id="None" and detach the entry from the row it describes."""
        org = Organization.objects.create(name="Temp", slug="temp-org")
        snapshot = AuditService.snapshot(org)
        org_id = org.pk

        org.delete()
        assert org.pk is None

        log = AuditService.log_delete(user=user, entity=org, old_value=snapshot)
        assert log.entity_id == str(org_id)

    def test_redaction_applies_to_any_snapshot_of_the_model(self, sdk_key):
        """A registry, not a per-call argument — a caller cannot forget it."""
        assert "hashed_key" not in AuditService.snapshot(sdk_key)
        assert "prefix" in AuditService.snapshot(sdk_key)
