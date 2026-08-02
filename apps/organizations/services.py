"""Business logic for organizations, memberships, and projects.

``AccessService`` is the shared authorization gate: other apps call it to assert
the caller's role is sufficient for a write. Role checks raise a 403
(``INSUFFICIENT_ROLE``); *membership* absence is handled upstream by the
membership-scoped queries, which 404 instead (an org/project you are not in is
invisible, not forbidden).
"""

from django.utils.text import slugify

from apps.core.errors import APIError, Error
from apps.organizations.models import Membership, Organization, Project, Role
from apps.organizations.queries import (
    MembershipQuery,
    OrganizationQuery,
    ProjectQuery,
)


class AccessService:
    """Role gate. Callers pass an already-resolved membership role, or the
    org id for the service to look it up."""

    @staticmethod
    def assert_min_role(user, organization_id, minimum: str) -> str:
        role = MembershipQuery.role_for(user, organization_id)
        if role is None:
            # Not a member — stay consistent with the "invisible" contract.
            raise APIError(Error.INSTANCE_NOT_FOUND, extra=["Organization"])
        if Role.rank(role) < Role.rank(minimum):
            raise APIError(Error.INSUFFICIENT_ROLE)
        return role

    @classmethod
    def assert_can_write(cls, user, project: Project) -> str:
        """Mutating flags/environments/rules/keys requires MEMBER or higher."""
        return cls.assert_min_role(user, project.organization_id, Role.MEMBER)

    @classmethod
    def assert_can_admin(cls, user, organization_id) -> str:
        """Managing members requires ADMIN or higher."""
        return cls.assert_min_role(user, organization_id, Role.ADMIN)

    @classmethod
    def assert_is_owner(cls, user, organization_id) -> str:
        return cls.assert_min_role(user, organization_id, Role.OWNER)


class OrganizationService:
    def create(self, user, name: str, slug: str = None) -> Organization:
        slug = self._unique_slug(slug or name)
        org = OrganizationQuery.create(name=name, slug=slug)
        MembershipQuery.create(organization=org, user=user, role=Role.OWNER)
        return org

    def delete(self, user, slug: str) -> None:
        org = OrganizationQuery.get_for_member(slug, user)
        AccessService.assert_is_owner(user, org.id)
        OrganizationQuery.delete(org)

    @staticmethod
    def _unique_slug(source: str) -> str:
        base = slugify(source)[:140] or "org"
        slug, i = base, 1
        while OrganizationQuery.slug_exists(slug):
            i += 1
            slug = f"{base}-{i}"
        return slug


class MembershipService:
    def add(self, actor, slug: str, user, role: str) -> Membership:
        # `user` is the target user's id (from MembershipWriteSerializer).
        org = OrganizationQuery.get_for_member(slug, actor)
        AccessService.assert_can_admin(actor, org.id)
        if MembershipQuery.role_for(user, org.id) is not None:
            raise APIError(Error.ALREADY_IN_STATE, extra=["User", "a member"])
        return MembershipQuery.create(organization=org, user_id=user, role=role)

    def change_role(self, actor, slug: str, user_id, role: str) -> Membership:
        org = OrganizationQuery.get_for_member(slug, actor)
        AccessService.assert_can_admin(actor, org.id)
        membership = MembershipQuery.get(org, user_id)
        # Never leave an org with zero owners.
        if membership.role == Role.OWNER and role != Role.OWNER:
            self._assert_not_last_owner(org)
        membership.role = role
        return MembershipQuery.save(membership, update_fields=["role", "updated_at"])

    def remove(self, actor, slug: str, user_id) -> None:
        org = OrganizationQuery.get_for_member(slug, actor)
        AccessService.assert_can_admin(actor, org.id)
        membership = MembershipQuery.get(org, user_id)
        if membership.role == Role.OWNER:
            self._assert_not_last_owner(org)
        MembershipQuery.delete(membership)

    @staticmethod
    def _assert_not_last_owner(org) -> None:
        if MembershipQuery.count_with_role(org, Role.OWNER) <= 1:
            raise APIError(Error.LAST_OWNER)


class ProjectService:
    def create(self, user, slug: str, name: str, key: str = None) -> Project:
        org = OrganizationQuery.get_for_member(slug, user)
        AccessService.assert_can_admin(user, org.id)
        return ProjectQuery.create(
            organization=org, name=name, key=self._unique_key(key or name)
        )

    def delete(self, user, key: str) -> None:
        project = ProjectQuery.get_for_member(key, user)
        AccessService.assert_can_admin(user, project.organization_id)
        ProjectQuery.delete(project)

    @staticmethod
    def _unique_key(source: str) -> str:
        base = slugify(source)[:140] or "project"
        key, i = base, 1
        while ProjectQuery.key_exists(key):
            i += 1
            key = f"{base}-{i}"
        return key
