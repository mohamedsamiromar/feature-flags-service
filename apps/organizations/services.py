"""Business logic for organizations, memberships, and projects.

``AccessService`` is the shared authorization gate: other apps call it to assert
the caller's role is sufficient for a write. Role checks raise a 403
(``INSUFFICIENT_ROLE``); *membership* absence is handled upstream by the
membership-scoped queries, which 404 instead (an org/project you are not in is
invisible, not forbidden).
"""

from django.utils.text import slugify

from apps.audit.services import AuditService
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
    """Audited on every mutation.

    An organization delete cascades to its projects, flags, environments, and
    SDK keys — the single most destructive call in the API, and the one whose
    "who did this" is hardest to reconstruct afterwards, because the rows it
    destroyed are gone.
    """

    def create(self, user, name: str, slug: str = None) -> Organization:
        slug = self._unique_slug(slug or name)
        org = OrganizationQuery.create(name=name, slug=slug)
        membership = MembershipQuery.create(
            organization=org, user=user, role=Role.OWNER
        )
        AuditService.log(
            user=user,
            action=AuditService.CREATE,
            entity=org,
            old_value=None,
            new_value=AuditService.snapshot(org),
        )
        AuditService.log(
            user=user,
            action=AuditService.CREATE,
            entity=membership,
            old_value=None,
            new_value=AuditService.snapshot(membership),
        )
        return org

    def delete(self, user, slug: str) -> None:
        org = OrganizationQuery.get_for_member(slug, user)
        AccessService.assert_is_owner(user, org.id)
        old_snapshot = AuditService.snapshot(org)
        OrganizationQuery.delete(org)
        AuditService.log_delete(user=user, entity=org, old_value=old_snapshot)

    @staticmethod
    def _unique_slug(source: str) -> str:
        base = slugify(source)[:140] or "org"
        slug, i = base, 1
        while OrganizationQuery.slug_exists(slug):
            i += 1
            slug = f"{base}-{i}"
        return slug


class MembershipService:
    """Audited on every mutation.

    Membership *is* the access-control boundary: adding one grants a stranger
    sight of every flag in the org, and a role change is a privilege change. The
    acting user recorded on each entry is the admin who made it, not the member
    it was made to — the trail answers "who granted this", which is the question
    that matters.
    """

    def add(self, actor, slug: str, user, role: str) -> Membership:
        # `user` is the target user's id (from MembershipWriteSerializer).
        org = OrganizationQuery.get_for_member(slug, actor)
        actor_role = AccessService.assert_can_admin(actor, org.id)
        self._assert_may_touch_owner_rank(actor_role, role)
        if MembershipQuery.role_for(user, org.id) is not None:
            raise APIError(Error.ALREADY_IN_STATE, extra=["User", "a member"])

        membership = MembershipQuery.create(
            organization=org, user_id=user, role=role
        )
        AuditService.log(
            user=actor,
            action=AuditService.CREATE,
            entity=membership,
            old_value=None,
            new_value=AuditService.snapshot(membership),
        )
        return membership

    def change_role(self, actor, slug: str, user_id, role: str) -> Membership:
        org = OrganizationQuery.get_for_member(slug, actor)
        actor_role = AccessService.assert_can_admin(actor, org.id)
        membership = MembershipQuery.get(org, user_id)
        self._assert_may_touch_owner_rank(actor_role, role, membership.role)
        # Never leave an org with zero owners.
        if membership.role == Role.OWNER and role != Role.OWNER:
            self._assert_not_last_owner(org)

        old_snapshot = AuditService.snapshot(membership)
        membership.role = role
        MembershipQuery.save(membership, update_fields=["role", "updated_at"])
        AuditService.log(
            user=actor,
            action=AuditService.UPDATE,
            entity=membership,
            old_value=old_snapshot,
            new_value=AuditService.snapshot(membership),
        )
        return membership

    def remove(self, actor, slug: str, user_id) -> None:
        org = OrganizationQuery.get_for_member(slug, actor)
        actor_role = AccessService.assert_can_admin(actor, org.id)
        membership = MembershipQuery.get(org, user_id)
        self._assert_may_touch_owner_rank(actor_role, membership.role)
        if membership.role == Role.OWNER:
            self._assert_not_last_owner(org)

        old_snapshot = AuditService.snapshot(membership)
        MembershipQuery.delete(membership)
        AuditService.log_delete(
            user=actor, entity=membership, old_value=old_snapshot
        )

    @staticmethod
    def _assert_may_touch_owner_rank(actor_role: str, *roles: str) -> None:
        """Only an owner may grant the owner role or change an owner's membership.

        ADMIN is enough to manage members, but not to reach the rank above it:
        an admin who could promote themselves would lift the last-owner guard,
        then demote or remove the real owner and delete the org. `roles` are
        every role the write involves — the one granted and the one replaced.
        """
        if Role.OWNER in roles and actor_role != Role.OWNER:
            raise APIError(Error.INSUFFICIENT_ROLE)

    @staticmethod
    def _assert_not_last_owner(org) -> None:
        if MembershipQuery.count_with_role(org, Role.OWNER) <= 1:
            raise APIError(Error.LAST_OWNER)


class ProjectService:
    """Audited on every mutation. A project delete takes its flags, segments,
    and environments with it."""

    def create(self, user, slug: str, name: str, key: str = None) -> Project:
        org = OrganizationQuery.get_for_member(slug, user)
        AccessService.assert_can_admin(user, org.id)
        project = ProjectQuery.create(
            organization=org, name=name, key=self._unique_key(key or name)
        )
        AuditService.log(
            user=user,
            action=AuditService.CREATE,
            entity=project,
            old_value=None,
            new_value=AuditService.snapshot(project),
        )
        return project

    def delete(self, user, key: str) -> None:
        project = ProjectQuery.get_for_member(key, user)
        AccessService.assert_can_admin(user, project.organization_id)
        old_snapshot = AuditService.snapshot(project)
        ProjectQuery.delete(project)
        AuditService.log_delete(user=user, entity=project, old_value=old_snapshot)

    @staticmethod
    def _unique_key(source: str) -> str:
        base = slugify(source)[:140] or "project"
        key, i = base, 1
        while ProjectQuery.key_exists(key):
            i += 1
            key = f"{base}-{i}"
        return key
