"""Data migration: give every pre-existing user a personal organization.

For each user we create an ``Organization`` ("Personal — {username}") with the
user as sole OWNER and a ``Default`` project, then reassign that user's flags
and environments onto the project. This preserves the previous per-user
isolation while moving the tenancy boundary to organization membership.

Reverse is a no-op: the schema-finalising migrations that follow drop ``owner``,
so there is nothing to restore the flags/environments back onto.
"""

from django.db import migrations
from django.utils.text import slugify


def _unique(base, taken):
    slug, i = base, 1
    while slug in taken:
        i += 1
        slug = f"{base}-{i}"
    taken.add(slug)
    return slug


def backfill(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    Organization = apps.get_model("organizations", "Organization")
    Membership = apps.get_model("organizations", "Membership")
    Project = apps.get_model("organizations", "Project")
    FeatureFlag = apps.get_model("flags", "FeatureFlag")
    Environment = apps.get_model("environment", "Environment")

    org_slugs = set()
    project_keys = set()

    for user in User.objects.all().iterator():
        base = slugify(user.username) or f"user-{user.pk}"
        org = Organization.objects.create(
            name=f"Personal — {user.username}",
            slug=_unique(base, org_slugs),
        )
        Membership.objects.create(organization=org, user=user, role="owner")
        project = Project.objects.create(
            organization=org,
            name="Default",
            key=_unique(f"{base}-default", project_keys),
        )
        FeatureFlag.objects.filter(owner=user).update(project=project)
        Environment.objects.filter(owner=user).update(project=project)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("organizations", "0001_initial"),
        ("flags", "0010_featureflag_project_alter_featureflag_owner"),
        ("environment", "0002_environment_project_alter_environment_id_and_more"),
    ]

    operations = [
        migrations.RunPython(backfill, noop),
    ]
