"""
Shared factories and fixtures used across all test modules.

Factories produce model instances with sensible defaults; override fields
by passing kwargs. Fixtures wire up the DRF test client and authenticate it.

Tenancy note: flags and environments belong to a *project*, not a user. To keep
the large existing test surface readable, ``FeatureFlagFactory`` and
``EnvironmentFactory`` accept an ``owner=<user>`` shim: the object is placed in
that user's deterministic personal project (auto-created with the user as
OWNER). Passing ``project=`` explicitly overrides the shim.
"""

import factory
import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.environment.models import Environment, EnvironmentFlag
from apps.flags.models import FeatureFlag, Variation
from apps.organizations.models import Membership, Organization, Project, Role
from apps.sdk_keys.key_generator import KeyGenerator
from apps.sdk_keys.models import SDKKey

User = get_user_model()


# ---------------------------------------------------------------------------
# Tenancy helpers
# ---------------------------------------------------------------------------

def personal_project_for(user, role=Role.OWNER) -> Project:
    """Deterministic per-user org + project so that a flag and an environment
    created with the same ``owner`` land in the *same* project."""
    org, _ = Organization.objects.get_or_create(
        slug=f"org-{user.id}", defaults={"name": f"Org {user.id}"}
    )
    Membership.objects.get_or_create(
        organization=org, user=user, defaults={"role": role}
    )
    # Reuse the org's existing project so a flag and an environment created for
    # the same user (via factory or the `project` fixture) share one project.
    project = Project.objects.filter(organization=org).first()
    if project is None:
        project = Project.objects.create(
            organization=org, name="Default", key=f"proj-{user.id}"
        )
    return project


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        skip_postgeneration_save = True

    username = factory.Sequence(lambda n: f"user{n}")
    email = factory.LazyAttribute(lambda o: f"{o.username}@test.com")
    password = factory.PostGenerationMethodCall("set_password", "testpass123")


class OrganizationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Organization

    name = factory.Sequence(lambda n: f"Org {n}")
    slug = factory.Sequence(lambda n: f"org-slug-{n}")


class MembershipFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Membership

    organization = factory.SubFactory(OrganizationFactory)
    user = factory.SubFactory(UserFactory)
    role = Role.MEMBER


class ProjectFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Project

    organization = factory.SubFactory(OrganizationFactory)
    name = factory.Sequence(lambda n: f"Project {n}")
    key = factory.Sequence(lambda n: f"project-{n}")


class _OwnerShimFactory(factory.django.DjangoModelFactory):
    """Base for project-scoped models that accept an ``owner=`` shim."""

    class Meta:
        abstract = True

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        owner = kwargs.pop("owner", None)
        if kwargs.get("project") is None:
            kwargs["project"] = personal_project_for(owner or UserFactory())
        return super()._create(model_class, *args, **kwargs)


class FeatureFlagFactory(_OwnerShimFactory):
    class Meta:
        model = FeatureFlag

    name = factory.Sequence(lambda n: f"Flag {n}")
    key = factory.Sequence(lambda n: f"flag-{n}")
    description = ""
    is_enabled = True
    rollout_percentage = 0
    is_archived = False


class EnvironmentFactory(_OwnerShimFactory):
    class Meta:
        model = Environment

    name = "production"


class EnvironmentFlagFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = EnvironmentFlag

    # feature_flag and environment must be provided explicitly so their
    # projects match — do NOT use SubFactory here or unique_together will fail.
    is_enabled = True
    rollout_percentage = 100


class VariationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Variation

    flag = factory.SubFactory(FeatureFlagFactory)
    name = factory.Sequence(lambda n: f"variation-{n}")
    value_type = Variation.ValueType.BOOLEAN
    value = True


class SDKKeyFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = SDKKey

    name = factory.Sequence(lambda n: f"SDK Key {n}")
    environment = factory.SubFactory(EnvironmentFactory)
    key_type = SDKKey.KeyType.SERVER
    is_active = True
    # Placeholder values; overridden in _create with a real generated key.
    prefix = "sdk_srv_test1234"
    hashed_key = factory.Sequence(lambda n: f"{'a' * 63}{n}"[:64])

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        key_type = kwargs.get("key_type", SDKKey.KeyType.SERVER)
        full_key, prefix, hashed = KeyGenerator.generate(key_type)
        kwargs["prefix"] = prefix
        kwargs["hashed_key"] = hashed
        instance = model_class.objects.create(**kwargs)
        # Attach the raw key for tests that need to authenticate with it.
        instance._full_key = full_key
        return instance


# ---------------------------------------------------------------------------
# Common fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def user(db):
    return UserFactory()


@pytest.fixture
def other_user(db):
    return UserFactory()


@pytest.fixture
def auth_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.fixture
def project(user, db):
    """The authenticated user's personal project (user is its OWNER)."""
    return personal_project_for(user)


@pytest.fixture
def base(project):
    """URL prefix for the auth user's flags: /api/v1/projects/{key}/flags."""
    return f"/api/v1/projects/{project.key}/flags"


@pytest.fixture
def env_base(project):
    """URL prefix for the auth user's environments."""
    return f"/api/v1/projects/{project.key}/environments"


@pytest.fixture
def flag(user, project, db):
    return FeatureFlagFactory(project=project)


@pytest.fixture
def environment(user, project, db):
    return EnvironmentFactory(project=project)


@pytest.fixture
def environment_flag(flag, environment, db):
    """EnvironmentFlag that links the shared flag + environment (same project)."""
    return EnvironmentFlagFactory(feature_flag=flag, environment=environment)


@pytest.fixture
def sdk_key(environment, db):
    """Active server SDK key attached to the shared environment."""
    return SDKKeyFactory(environment=environment)


@pytest.fixture(autouse=True)
def _clear_flag_cache():
    """Give every test an empty cache.

    Tests run against a real Redis instance that outlives the test database.
    Since the DB is recreated per session, primary keys restart and a cache key
    (``flags:{project_id}:{env_id}:{flag_key}``) can collide with an entry left
    behind by an earlier run — so a test asserting on a cache miss would read a
    stale value from a previous session. Clearing before and after keeps each
    test hermetic and stops one test's cache writes leaking into the next.
    """
    from django.core.cache import cache

    _try_clear(cache)
    yield
    _try_clear(cache)


def _try_clear(cache) -> None:
    """Clear the cache, tolerating an unreachable Redis.

    Tests that actually exercise caching still fail loudly on their own when
    Redis is down. Swallowing the error here only keeps an autouse fixture from
    turning every pure-logic test in the repo into a setup error too.
    """
    try:
        cache.clear()
    except Exception:  # noqa: BLE001 — any backend/connection error is non-fatal here
        pass
