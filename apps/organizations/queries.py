"""Query layer for the organizations app — the only place with ORM access for
organizations, memberships, and projects.

``ProjectQuery.get_for_member`` and ``MembershipQuery.role_for`` are the shared
entry points other apps use to enforce the membership-based tenancy boundary.
A project the caller cannot see surfaces as a 404, matching the API's existing
"not mine is invisible" contract.
"""

from apps.core.errors import APIError, Error
from apps.organizations.models import Membership, Organization, Project


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
