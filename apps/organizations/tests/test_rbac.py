"""
Role-based access control matrix + cross-project isolation for flag writes.

Exercises the shared ``AccessService`` gate through the flag endpoints:
  - VIEWER  → read yes, write no (403)
  - MEMBER  → write yes
  - a user in a *different* project → flag invisible (404)
"""

import pytest

from apps.organizations.models import Membership, Role
from conftest import (
    FeatureFlagFactory,
    MembershipFactory,
    ProjectFactory,
    UserFactory,
)


def _flags_url(project):
    return f"/api/v1/projects/{project.key}/flags"


def _client(user):
    from rest_framework.test import APIClient
    c = APIClient()
    c.force_authenticate(user)
    return c


@pytest.fixture
def project_with_flag(db):
    """A project (in some org) holding one flag, plus the org for role setup."""
    project = ProjectFactory()
    flag = FeatureFlagFactory(project=project, key="dark-mode")
    return project, flag


def _member(project, role):
    user = UserFactory()
    MembershipFactory(organization=project.organization, user=user, role=role)
    return user


@pytest.mark.django_db
class TestRoleMatrix:
    def test_viewer_can_read_flag(self, project_with_flag):
        project, flag = project_with_flag
        viewer = _member(project, Role.VIEWER)
        resp = _client(viewer).get(f"{_flags_url(project)}/{flag.key}/")
        assert resp.status_code == 200

    def test_viewer_cannot_update_flag(self, project_with_flag):
        project, flag = project_with_flag
        viewer = _member(project, Role.VIEWER)
        resp = _client(viewer).patch(
            f"{_flags_url(project)}/{flag.key}/",
            {"name": "Changed"},
            format="json",
        )
        assert resp.status_code == 403

    def test_viewer_cannot_create_flag(self, project_with_flag):
        project, _ = project_with_flag
        viewer = _member(project, Role.VIEWER)
        resp = _client(viewer).post(
            f"{_flags_url(project)}/",
            {"name": "New", "key": "new-flag"},
            format="json",
        )
        assert resp.status_code == 403

    def test_member_can_update_flag(self, project_with_flag):
        project, flag = project_with_flag
        member = _member(project, Role.MEMBER)
        resp = _client(member).patch(
            f"{_flags_url(project)}/{flag.key}/",
            {"name": "Changed"},
            format="json",
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Changed"

    def test_member_can_toggle_flag(self, project_with_flag):
        project, flag = project_with_flag
        from conftest import EnvironmentFactory
        EnvironmentFactory(project=project, name="production")
        member = _member(project, Role.MEMBER)
        resp = _client(member).post(
            f"{_flags_url(project)}/{flag.key}/toggle/",
            {"environment": "production"},
            format="json",
        )
        assert resp.status_code == 200

    def test_admin_can_create_flag(self, project_with_flag):
        project, _ = project_with_flag
        admin = _member(project, Role.ADMIN)
        resp = _client(admin).post(
            f"{_flags_url(project)}/",
            {"name": "New", "key": "admin-flag"},
            format="json",
        )
        assert resp.status_code == 201


@pytest.mark.django_db
class TestCrossProjectIsolation:
    def test_foreign_user_cannot_read_flag(self, project_with_flag):
        project, flag = project_with_flag
        outsider = UserFactory()  # no membership anywhere
        resp = _client(outsider).get(f"{_flags_url(project)}/{flag.key}/")
        assert resp.status_code == 404

    def test_foreign_user_cannot_list_flags(self, project_with_flag):
        project, _ = project_with_flag
        outsider = UserFactory()
        resp = _client(outsider).get(f"{_flags_url(project)}/")
        assert resp.status_code == 404

    def test_member_of_other_project_cannot_reach_flag(self, project_with_flag):
        project, flag = project_with_flag
        other_project = ProjectFactory()
        member = _member(other_project, Role.OWNER)
        # Uses the victim project's key in the URL, but caller only belongs to
        # a different project → 404.
        resp = _client(member).get(f"{_flags_url(project)}/{flag.key}/")
        assert resp.status_code == 404

    def test_flag_keys_are_unique_per_project_not_globally(self, project_with_flag):
        """Two projects may each hold a flag with the same key."""
        project, flag = project_with_flag  # key="dark-mode"
        other_project = ProjectFactory()
        # Must not raise IntegrityError — key is unique per project.
        twin = FeatureFlagFactory(project=other_project, key="dark-mode")
        assert twin.key == flag.key
        assert twin.project_id != flag.project_id


@pytest.mark.django_db
class TestOwnerRankIsOwnerOnly:
    """Only an owner may grant the owner role or touch an owner's membership.

    Without this an admin promotes themselves, which lifts the last-owner guard,
    then demotes or removes the original owner and can delete the org.
    """

    @pytest.fixture
    def org(self, db):
        project = ProjectFactory()
        owner = _member(project, Role.OWNER)
        admin = _member(project, Role.ADMIN)
        return project.organization, owner, admin

    def _members_url(self, org, user_id=None):
        url = f"/api/v1/organizations/{org.slug}/members/"
        return f"{url}{user_id}/" if user_id else url

    def _role(self, org, user):
        return Membership.objects.get(organization=org, user=user).role

    def test_admin_cannot_promote_self_to_owner(self, org):
        org, _, admin = org
        resp = _client(admin).patch(
            self._members_url(org, admin.id), {"role": Role.OWNER}, format="json"
        )
        assert resp.status_code == 403
        assert self._role(org, admin) == Role.ADMIN

    def test_admin_cannot_demote_an_owner(self, org):
        org, owner, admin = org
        # A second owner, so the last-owner guard is not what stops this.
        _member_in_org(org, Role.OWNER)
        resp = _client(admin).patch(
            self._members_url(org, owner.id), {"role": Role.VIEWER}, format="json"
        )
        assert resp.status_code == 403
        assert self._role(org, owner) == Role.OWNER

    def test_admin_cannot_remove_an_owner(self, org):
        org, owner, admin = org
        _member_in_org(org, Role.OWNER)
        resp = _client(admin).delete(self._members_url(org, owner.id))
        assert resp.status_code == 403
        assert self._role(org, owner) == Role.OWNER

    def test_owner_can_grant_owner(self, org):
        org, owner, admin = org
        resp = _client(owner).patch(
            self._members_url(org, admin.id), {"role": Role.OWNER}, format="json"
        )
        assert resp.status_code == 200
        assert self._role(org, admin) == Role.OWNER

    def test_admin_still_manages_non_owners(self, org):
        org, _, admin = org
        member = _member_in_org(org, Role.MEMBER)
        resp = _client(admin).patch(
            self._members_url(org, member.id), {"role": Role.VIEWER}, format="json"
        )
        assert resp.status_code == 200


def _member_in_org(org, role):
    user = UserFactory()
    MembershipFactory(organization=org, user=user, role=role)
    return user
