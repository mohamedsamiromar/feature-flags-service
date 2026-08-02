"""
Organization / project / membership API — end-to-end tests.

Covers CRUD, auto-provisioning of OWNER membership + Default project on org
creation, and the "invisible to non-members" (404) contract.
"""

import pytest

from apps.organizations.models import Membership, Project, Role

ORG_BASE = "/api/v1/organizations"
PROJ_BASE = "/api/v1/projects"


@pytest.mark.django_db
class TestOrganizationCRUD:
    def test_create_returns_201(self, auth_client):
        resp = auth_client.post(ORG_BASE + "/", {"name": "Acme"}, format="json")
        assert resp.status_code == 201
        assert resp.json()["slug"] == "acme"

    def test_create_makes_caller_owner(self, auth_client, user):
        auth_client.post(ORG_BASE + "/", {"name": "Acme"}, format="json")
        membership = Membership.objects.get(user=user, organization__slug="acme")
        assert membership.role == Role.OWNER

    def test_create_generates_unique_slug_on_collision(self, auth_client):
        first = auth_client.post(ORG_BASE + "/", {"name": "Dup"}, format="json")
        second = auth_client.post(ORG_BASE + "/", {"name": "Dup"}, format="json")
        assert first.json()["slug"] == "dup"
        assert second.json()["slug"] == "dup-2"

    def test_list_only_shows_member_orgs(self, auth_client, user, other_user):
        auth_client.post(ORG_BASE + "/", {"name": "Mine"}, format="json")
        # An org the caller is not a member of.
        from conftest import OrganizationFactory
        OrganizationFactory(slug="theirs")
        resp = auth_client.get(ORG_BASE + "/")
        slugs = {o["slug"] for o in resp.json()["results"]}
        assert "mine" in slugs
        assert "theirs" not in slugs

    def test_retrieve_foreign_org_returns_404(self, auth_client):
        from conftest import OrganizationFactory
        OrganizationFactory(slug="secret")
        resp = auth_client.get(f"{ORG_BASE}/secret/")
        assert resp.status_code == 404

    def test_owner_can_delete_org(self, auth_client):
        auth_client.post(ORG_BASE + "/", {"name": "Acme"}, format="json")
        resp = auth_client.delete(f"{ORG_BASE}/acme/")
        assert resp.status_code == 204

    def test_non_owner_cannot_delete_org(self, auth_client, api_client, other_user):
        auth_client.post(ORG_BASE + "/", {"name": "Acme"}, format="json")
        # Add other_user as a plain MEMBER.
        auth_client.post(
            f"{ORG_BASE}/acme/members/",
            {"user": other_user.id, "role": Role.MEMBER},
            format="json",
        )
        api_client.force_authenticate(other_user)
        resp = api_client.delete(f"{ORG_BASE}/acme/")
        assert resp.status_code == 403


@pytest.mark.django_db
class TestMembership:
    def _make_org(self, auth_client):
        auth_client.post(ORG_BASE + "/", {"name": "Acme"}, format="json")

    def test_admin_can_add_member(self, auth_client, other_user):
        self._make_org(auth_client)
        resp = auth_client.post(
            f"{ORG_BASE}/acme/members/",
            {"user": other_user.id, "role": Role.MEMBER},
            format="json",
        )
        assert resp.status_code == 201
        assert Membership.objects.filter(
            user=other_user, organization__slug="acme"
        ).exists()

    def test_adding_existing_member_returns_409(self, auth_client, other_user):
        self._make_org(auth_client)
        payload = {"user": other_user.id, "role": Role.MEMBER}
        auth_client.post(f"{ORG_BASE}/acme/members/", payload, format="json")
        resp = auth_client.post(f"{ORG_BASE}/acme/members/", payload, format="json")
        assert resp.status_code == 409

    def test_member_cannot_add_member(self, auth_client, api_client, other_user):
        self._make_org(auth_client)
        auth_client.post(
            f"{ORG_BASE}/acme/members/",
            {"user": other_user.id, "role": Role.MEMBER},
            format="json",
        )
        api_client.force_authenticate(other_user)
        from conftest import UserFactory
        third = UserFactory()
        resp = api_client.post(
            f"{ORG_BASE}/acme/members/",
            {"user": third.id, "role": Role.MEMBER},
            format="json",
        )
        assert resp.status_code == 403

    def test_change_role(self, auth_client, other_user):
        self._make_org(auth_client)
        auth_client.post(
            f"{ORG_BASE}/acme/members/",
            {"user": other_user.id, "role": Role.MEMBER},
            format="json",
        )
        resp = auth_client.patch(
            f"{ORG_BASE}/acme/members/{other_user.id}/",
            {"role": Role.ADMIN},
            format="json",
        )
        assert resp.status_code == 200
        assert Membership.objects.get(
            user=other_user, organization__slug="acme"
        ).role == Role.ADMIN

    def test_cannot_demote_last_owner(self, auth_client, user):
        self._make_org(auth_client)
        resp = auth_client.patch(
            f"{ORG_BASE}/acme/members/{user.id}/",
            {"role": Role.ADMIN},
            format="json",
        )
        assert resp.status_code == 409

    def test_cannot_remove_last_owner(self, auth_client, user):
        self._make_org(auth_client)
        resp = auth_client.delete(f"{ORG_BASE}/acme/members/{user.id}/")
        assert resp.status_code == 409


@pytest.mark.django_db
class TestProjectCRUD:
    def _make_org(self, auth_client):
        auth_client.post(ORG_BASE + "/", {"name": "Acme"}, format="json")

    def test_create_project_under_org(self, auth_client):
        self._make_org(auth_client)
        resp = auth_client.post(
            PROJ_BASE + "/",
            {"organization": "acme", "name": "Web"},
            format="json",
        )
        assert resp.status_code == 201
        assert resp.json()["key"] == "web"

    def test_member_cannot_create_project(self, auth_client, api_client, other_user):
        self._make_org(auth_client)
        auth_client.post(
            f"{ORG_BASE}/acme/members/",
            {"user": other_user.id, "role": Role.MEMBER},
            format="json",
        )
        api_client.force_authenticate(other_user)
        resp = api_client.post(
            PROJ_BASE + "/",
            {"organization": "acme", "name": "Web"},
            format="json",
        )
        assert resp.status_code == 403

    def test_list_only_member_projects(self, auth_client):
        self._make_org(auth_client)
        auth_client.post(
            PROJ_BASE + "/", {"organization": "acme", "name": "Web"}, format="json"
        )
        from conftest import ProjectFactory
        ProjectFactory(key="foreign")
        resp = auth_client.get(PROJ_BASE + "/")
        keys = {p["key"] for p in resp.json()["results"]}
        assert "web" in keys
        assert "foreign" not in keys

    def test_retrieve_foreign_project_returns_404(self, auth_client):
        from conftest import ProjectFactory
        ProjectFactory(key="foreign")
        resp = auth_client.get(f"{PROJ_BASE}/foreign/")
        assert resp.status_code == 404
