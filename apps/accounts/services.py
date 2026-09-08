"""Business logic for self-serve registration.

Registration is not just a user row. The tenancy boundary is the *project*, and
a project only exists inside an organization, so a user with no organization
can do nothing at all — no flags, no environments, no SDK keys. Signing up
therefore provisions the same shape the
``organizations/0002_backfill_personal_orgs`` data migration gave every
pre-existing user: a personal organization owned by the new user, and a
``Default`` project inside it.

It goes one step further than that migration and creates the three standard
environments. The migration did not need to — it was moving flags that already
had environments. A brand-new project has none, and without one there is
nothing to issue an SDK key against or evaluate a flag in, so a caller would
have to make three more requests before the API did anything useful.
"""

from django.db import IntegrityError, transaction

from apps.accounts.queries import UserQuery
from apps.audit.services import AuditService
from apps.core.errors import APIError, Error
from apps.environment.models import Environment
from apps.environment.queries import EnvironmentQuery
from apps.organizations.models import Role
from apps.organizations.queries import (
    MembershipQuery,
    OrganizationQuery,
    ProjectQuery,
)
from apps.organizations.services import OrganizationService, ProjectService


class RegistrationService:
    def register(self, *, username: str, email: str, password: str) -> dict:
        """Create the user and their personal tenancy in one transaction.

        Atomic on purpose: a user with no organization, or an organization with
        no project, is a dead account that the API gives no way to repair. All
        of it commits or none of it does.
        """
        try:
            with transaction.atomic():
                user = UserQuery.create(
                    username=username, email=email, password=password
                )
                organization = OrganizationQuery.create(
                    name=f"Personal — {username}",
                    slug=OrganizationService._unique_slug(username),
                )
                membership = MembershipQuery.create(
                    organization=organization, user=user, role=Role.OWNER
                )
                project = ProjectQuery.create(
                    organization=organization,
                    name="Default",
                    key=ProjectService._unique_key(f"{username}-default"),
                )
                environments = [
                    EnvironmentQuery.create(project=project, name=name.value)
                    for name in Environment.EnvironmentName
                ]
                # Audited like any other org and project creation. Signup is the
                # one path that builds them without going through
                # OrganizationService/ProjectService, and an audit invariant
                # with an exception in it is the one that gets discovered
                # during an incident.
                for entity in (organization, membership, project, *environments):
                    AuditService.log(
                        user=user,
                        action=AuditService.CREATE,
                        entity=entity,
                        old_value=None,
                        new_value=AuditService.snapshot(entity),
                    )
        except IntegrityError:
            # Every unique constraint reachable inside that transaction is
            # derived from the username — the username itself, the org slug
            # (`slugify(username)`), and the project key (`{username}-default`).
            # `_unique_slug` / `_unique_key` check-then-create, so a second
            # request racing the first can still lose at the database. The
            # remedy is the same for all three: pick another username.
            raise APIError(Error.USERNAME_TAKEN, extra=[username])

        return {
            "user": user,
            "organization": organization,
            "project": project,
            "environments": environments,
        }
