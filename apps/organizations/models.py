from django.conf import settings
from django.db import models

from apps.core.models import BaseModel


class Role(models.TextChoices):
    """Ordered least→most privileged. Comparisons use ``Role.rank``."""

    VIEWER = "viewer", "Viewer"
    MEMBER = "member", "Member"
    ADMIN = "admin", "Admin"
    OWNER = "owner", "Owner"

    @classmethod
    def rank(cls, value: str) -> int:
        order = [cls.VIEWER, cls.MEMBER, cls.ADMIN, cls.OWNER]
        return order.index(cls(value))


class Organization(BaseModel):
    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=150, unique=True, db_index=True)

    def __str__(self):
        return self.slug


class Membership(BaseModel):
    """Binds a user to an organization with a role. This is the multi-tenancy
    boundary: access to a project is granted iff the user has a membership in
    that project's organization."""

    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.MEMBER,
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "user"],
                name="unique_membership_per_org",
            ),
        ]

    def __str__(self):
        return f"{self.user_id}@{self.organization.slug}:{self.role}"


class Project(BaseModel):
    organization = models.ForeignKey(
        Organization,
        on_delete=models.CASCADE,
        related_name="projects",
    )
    name = models.CharField(max_length=150)
    # Globally unique so a project is addressable as /projects/{key}/ without
    # also threading the org slug through every flag and environment URL.
    key = models.SlugField(max_length=150, unique=True, db_index=True)

    def __str__(self):
        return f"{self.organization.slug}/{self.key}"
