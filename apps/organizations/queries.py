"""Query layer for the organizations app — the only place with ORM access for
organizations, memberships, and projects.

``ProjectQuery.get_for_member`` and ``MembershipQuery.role_for`` are the shared
entry points other apps use to enforce the membership-based tenancy boundary.
A project the caller cannot see surfaces as a 404, matching the API's existing
"not mine is invisible" contract.
"""

from apps.core.errors import APIError, Error
from apps.organizations.models import Invitation, Membership, Organization, Project


class OrganizationQuery:
    @staticmethod
    def get_for_member(slug: str, user) -> Organization:
        try:
            return Organization.objects.get(slug=slug, memberships__user=user)
        except Organization.DoesNotExist:
            raise APIError(Error.INSTANCE_NOT_FOUND, extra=["Organization"])

    @staticmethod
    def list_for_member(user):
        return Organization.objects.filter(memberships__user=user).order_by("name")

    @staticmethod
    def create(**fields) -> Organization:
        return Organization.objects.create(**fields)

    @staticmethod
    def delete(organization: Organization) -> None:
        organization.delete()

    @staticmethod
    def slug_exists(slug: str) -> bool:
        return Organization.objects.filter(slug=slug).exists()


class MembershipQuery:
    @staticmethod
    def role_for(user, organization_id) -> str:
        """Return the caller's role in the org, or None if not a member."""
        return (
            Membership.objects
            .filter(organization_id=organization_id, user=user)
            .values_list("role", flat=True)
            .first()
        )

    @staticmethod
    def list_for_org(organization):
        return (
            Membership.objects
            .filter(organization=organization)
            .select_related("user")
            .order_by("created_at")
        )

    @staticmethod
    def get(organization, user_id) -> Membership:
        try:
            return Membership.objects.get(organization=organization, user_id=user_id)
        except Membership.DoesNotExist:
            raise APIError(Error.INSTANCE_NOT_FOUND, extra=["Membership"])

    @staticmethod
    def create(**fields) -> Membership:
        return Membership.objects.create(**fields)

    @staticmethod
    def save(membership: Membership, update_fields=None) -> Membership:
        membership.save(update_fields=update_fields)
        return membership

    @staticmethod
    def delete(membership: Membership) -> None:
        membership.delete()

    @staticmethod
    def count_with_role(organization, role: str) -> int:
        return Membership.objects.filter(organization=organization, role=role).count()


class InvitationQuery:
    """Every lookup is pending-only and scoped to one side of the invitation —
    the org (for admins) or the invitee (for the user deciding). A decided
    invitation, or someone else's, is a 404: there is nothing to act on."""

    @staticmethod
    def pending_for_org(organization):
        return (
            Invitation.objects
            .filter(organization=organization, status=Invitation.Status.PENDING)
            .select_related("organization", "invitee", "invited_by")
            .order_by("-created_at")
        )

    @staticmethod
    def pending_for_invitee(user):
        return (
            Invitation.objects
            .filter(invitee=user, status=Invitation.Status.PENDING)
            .select_related("organization", "invitee", "invited_by")
            .order_by("-created_at")
        )

    @staticmethod
    def get_pending_in_org(organization, invitation_id) -> Invitation:
        try:
            return InvitationQuery.pending_for_org(organization).get(pk=invitation_id)
        except (Invitation.DoesNotExist, ValueError):
            raise APIError(Error.INSTANCE_NOT_FOUND, extra=["Invitation"])

    @staticmethod
    def get_pending_for_invitee(invitation_id, user, lock: bool = False) -> Invitation:
        """`lock` takes a row lock (inside the caller's transaction) so two
        concurrent accepts cannot both create a membership."""
        qs = InvitationQuery.pending_for_invitee(user)
        if lock:
            qs = qs.select_for_update(of=("self",))
        try:
            return qs.get(pk=invitation_id)
        except (Invitation.DoesNotExist, ValueError):
            raise APIError(Error.INSTANCE_NOT_FOUND, extra=["Invitation"])

    @staticmethod
    def pending_exists(organization, user) -> bool:
        return Invitation.objects.filter(
            organization=organization, invitee=user, status=Invitation.Status.PENDING
        ).exists()

    @staticmethod
    def create(**fields) -> Invitation:
        return Invitation.objects.create(**fields)

    @staticmethod
    def save(invitation: Invitation, update_fields=None) -> Invitation:
        invitation.save(update_fields=update_fields)
        return invitation


class ProjectQuery:
    @staticmethod
    def get_for_member(key: str, user) -> Project:
        """Owner-agnostic, membership-scoped fetch. Missing or not a member → 404."""
        try:
            return (
                Project.objects
                .select_related("organization")
                .get(key=key, organization__memberships__user=user)
            )
        except Project.DoesNotExist:
            raise APIError(Error.INSTANCE_NOT_FOUND, extra=["Project"])

    @staticmethod
    def list_for_org(organization):
        return Project.objects.filter(organization=organization).order_by("name")

    @staticmethod
    def list_for_member(user):
        return (
            Project.objects
            .filter(organization__memberships__user=user)
            .select_related("organization")
            .order_by("name")
        )

    @staticmethod
    def create(**fields) -> Project:
        return Project.objects.create(**fields)

    @staticmethod
    def delete(project: Project) -> None:
        project.delete()

    @staticmethod
    def key_exists(key: str) -> bool:
        return Project.objects.filter(key=key).exists()
