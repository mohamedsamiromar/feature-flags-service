"""
F-03: SDKKeyService — unit tests.

Tests cover create, revoke, and rotate operations, including ownership
enforcement and the invariant that the raw key is never stored.
"""

import pytest
from django.core.exceptions import PermissionDenied

from apps.sdk_keys.key_generator import KeyGenerator
from apps.sdk_keys.models import SDKKey
from apps.sdk_keys.services import SDKKeyService
from conftest import EnvironmentFactory, SDKKeyFactory, UserFactory

service = SDKKeyService()


@pytest.mark.django_db
class TestCreateKey:
    def test_returns_sdk_key_instance_and_full_key(self, user, environment):
        sdk_key, full_key = service.create_key(
            user=user,
            environment_id=environment.id,
            name="My Key",
            key_type=SDKKey.KeyType.SERVER,
        )
        assert isinstance(sdk_key, SDKKey)
        assert isinstance(full_key, str)
        assert full_key.startswith("sdk_srv_")

    def test_full_key_not_stored_in_db(self, user, environment):
        sdk_key, full_key = service.create_key(
            user=user,
            environment_id=environment.id,
            name="My Key",
            key_type=SDKKey.KeyType.SERVER,
        )
        refreshed = SDKKey.objects.get(pk=sdk_key.pk)
        assert refreshed.hashed_key != full_key
        assert refreshed.prefix != full_key
        # The full key should not appear in any stored field
        assert full_key not in str(refreshed.__dict__.values())

    def test_stored_hash_matches_full_key(self, user, environment):
        sdk_key, full_key = service.create_key(
            user=user,
            environment_id=environment.id,
            name="My Key",
            key_type=SDKKey.KeyType.SERVER,
        )
        assert sdk_key.hashed_key == KeyGenerator.hash_raw(full_key)

    def test_raises_if_environment_not_owned_by_user(self, user):
        other_env = EnvironmentFactory()  # different owner
        with pytest.raises(Exception):
            service.create_key(
                user=user,
                environment_id=other_env.id,
                name="Bad Key",
                key_type=SDKKey.KeyType.SERVER,
            )

    def test_client_key_starts_with_correct_prefix(self, user, environment):
        _, full_key = service.create_key(
            user=user,
            environment_id=environment.id,
            name="Frontend Key",
            key_type=SDKKey.KeyType.CLIENT,
        )
        assert full_key.startswith("sdk_cli_")

    def test_key_is_active_by_default(self, user, environment):
        sdk_key, _ = service.create_key(
            user=user,
            environment_id=environment.id,
            name="My Key",
            key_type=SDKKey.KeyType.SERVER,
        )
        assert sdk_key.is_active is True


@pytest.mark.django_db
class TestRevokeKey:
    def test_revoke_sets_is_active_false(self, sdk_key, user, environment):
        service.revoke(sdk_key=sdk_key, user=environment.owner)
        sdk_key.refresh_from_db()
        assert sdk_key.is_active is False

    def test_revoke_persists_to_db(self, sdk_key, environment):
        service.revoke(sdk_key=sdk_key, user=environment.owner)
        refreshed = SDKKey.objects.get(pk=sdk_key.pk)
        assert refreshed.is_active is False

    def test_revoke_raises_permission_denied_for_non_owner(self, sdk_key):
        other_user = UserFactory()
        with pytest.raises(PermissionDenied):
            service.revoke(sdk_key=sdk_key, user=other_user)

    def test_revoke_returns_sdk_key(self, sdk_key, environment):
        result = service.revoke(sdk_key=sdk_key, user=environment.owner)
        assert result.pk == sdk_key.pk
        assert result.is_active is False


@pytest.mark.django_db
class TestRotateKey:
    def test_rotate_deactivates_old_key(self, sdk_key, environment):
        service.rotate(sdk_key=sdk_key, user=environment.owner)
        sdk_key.refresh_from_db()
        assert sdk_key.is_active is False

    def test_rotate_creates_new_key(self, sdk_key, environment):
        new_key, _ = service.rotate(sdk_key=sdk_key, user=environment.owner)
        assert new_key.pk != sdk_key.pk
        assert new_key.is_active is True

    def test_rotate_returns_new_full_key(self, sdk_key, environment):
        new_key, full_key = service.rotate(sdk_key=sdk_key, user=environment.owner)
        assert full_key.startswith("sdk_srv_")
        assert new_key.hashed_key == KeyGenerator.hash_raw(full_key)

    def test_rotate_preserves_name_and_key_type(self, environment):
        original = SDKKeyFactory(
            environment=environment,
            name="Prod Key",
            key_type=SDKKey.KeyType.SERVER,
        )
        new_key, _ = service.rotate(sdk_key=original, user=environment.owner)
        assert new_key.name == original.name
        assert new_key.key_type == original.key_type
        assert new_key.environment_id == original.environment_id

    def test_rotate_raises_permission_denied_for_non_owner(self, sdk_key):
        with pytest.raises(PermissionDenied):
            service.rotate(sdk_key=sdk_key, user=UserFactory())
