"""
Shared factories and fixtures used across all test modules.

Factories produce model instances with sensible defaults; override fields
by passing kwargs. Fixtures wire up the DRF test client and authenticate it.
"""

import factory
import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.environment.models import Environment, EnvironmentFlag
from apps.flags.models import FeatureFlag
from apps.sdk_keys.key_generator import KeyGenerator
from apps.sdk_keys.models import SDKKey

User = get_user_model()


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


class FeatureFlagFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = FeatureFlag

    owner = factory.SubFactory(UserFactory)
    name = factory.Sequence(lambda n: f"Flag {n}")
    key = factory.Sequence(lambda n: f"flag-{n}")
    description = ""
    is_enabled = True
    rollout_percentage = 0
    is_archived = False


class EnvironmentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Environment

    owner = factory.SubFactory(UserFactory)
    name = "production"


class EnvironmentFlagFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = EnvironmentFlag

    # feature_flag and environment must be provided explicitly so their
    # owners match — do NOT use SubFactory here or unique_together will fail.
    is_enabled = True
    rollout_percentage = 100


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
def flag(user, db):
    return FeatureFlagFactory(owner=user)


@pytest.fixture
def environment(user, db):
    return EnvironmentFactory(owner=user)


@pytest.fixture
def environment_flag(flag, environment, db):
    """EnvironmentFlag that links the shared flag + environment (same owner)."""
    return EnvironmentFlagFactory(feature_flag=flag, environment=environment)


@pytest.fixture
def sdk_key(environment, db):
    """Active server SDK key attached to the shared environment."""
    return SDKKeyFactory(environment=environment)
