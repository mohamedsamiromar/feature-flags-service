"""
F-03: SDK key management API — end-to-end tests.

Covers: create, list, revoke, rotate, ownership isolation.
"""

import pytest

from apps.sdk_keys.key_generator import KeyGenerator
from apps.sdk_keys.models import SDKKey
from conftest import EnvironmentFactory, SDKKeyFactory, UserFactory

BASE = "/api/v1/sdk-keys"


@pytest.mark.django_db
class TestCreateSDKKey:
    def test_create_returns_201(self, auth_client, environment):
        resp = auth_client.post(
            f"{BASE}/",
            {"name": "My Key", "key_type": "server", "environment": environment.id},
            format="json",
        )
        assert resp.status_code == 201

    def test_create_returns_full_key_once(self, auth_client, environment):
        resp = auth_client.post(
            f"{BASE}/",
            {"name": "My Key", "key_type": "server", "environment": environment.id},
            format="json",
        )
        data = resp.json()
        assert "key" in data
        assert data["key"].startswith("sdk_srv_")

    def test_create_returns_correct_key_type(self, auth_client, environment):
        resp = auth_client.post(
            f"{BASE}/",
            {"name": "Frontend", "key_type": "client", "environment": environment.id},
            format="json",
        )
        assert resp.json()["key_type"] == "client"
        assert resp.json()["key"].startswith("sdk_cli_")

    def test_create_wrong_environment_returns_400(self, auth_client):
        other_env = EnvironmentFactory()  # different owner
        resp = auth_client.post(
            f"{BASE}/",
            {"name": "Bad Key", "key_type": "server", "environment": other_env.id},
            format="json",
        )
        assert resp.status_code == 400

    def test_create_stores_hash_not_raw_key(self, auth_client, environment):
        resp = auth_client.post(
            f"{BASE}/",
            {"name": "My Key", "key_type": "server", "environment": environment.id},
            format="json",
        )
        full_key = resp.json()["key"]
        db_key = SDKKey.objects.get(environment=environment)
        assert db_key.hashed_key == KeyGenerator.hash_raw(full_key)
        assert db_key.hashed_key != full_key

    def test_unauthenticated_create_returns_401(self, api_client, environment):
        resp = api_client.post(
            f"{BASE}/",
            {"name": "My Key", "key_type": "server", "environment": environment.id},
            format="json",
        )
        assert resp.status_code == 401


@pytest.mark.django_db
class TestListSDKKeys:
    def test_list_does_not_return_full_key(self, auth_client, sdk_key):
        resp = auth_client.get(f"{BASE}/")
        result = resp.json()["results"][0]
        assert "key" not in result
        assert "hashed_key" not in result

    def test_list_returns_prefix(self, auth_client, sdk_key):
        resp = auth_client.get(f"{BASE}/")
        result = resp.json()["results"][0]
        assert "prefix" in result
        assert result["prefix"] == sdk_key.prefix

    def test_list_only_shows_own_keys(self, auth_client, user, environment):
        SDKKeyFactory(environment=environment)  # user's key
        SDKKeyFactory()  # another user's key
        resp = auth_client.get(f"{BASE}/")
        assert resp.json()["count"] == 1

    def test_list_includes_revoked_keys(self, auth_client, environment):
        SDKKeyFactory(environment=environment, is_active=True)
        revoked = SDKKeyFactory(environment=environment)
        revoked.is_active = False
        revoked.save()
        resp = auth_client.get(f"{BASE}/")
        assert resp.json()["count"] == 2


@pytest.mark.django_db
class TestRevokeSDKKey:
    def test_revoke_returns_200(self, auth_client, sdk_key):
        resp = auth_client.post(f"{BASE}/{sdk_key.id}/revoke/")
        assert resp.status_code == 200

    def test_revoke_sets_is_active_false(self, auth_client, sdk_key):
        auth_client.post(f"{BASE}/{sdk_key.id}/revoke/")
        sdk_key.refresh_from_db()
        assert sdk_key.is_active is False

    def test_double_revoke_returns_409(self, auth_client, sdk_key):
        auth_client.post(f"{BASE}/{sdk_key.id}/revoke/")
        resp = auth_client.post(f"{BASE}/{sdk_key.id}/revoke/")
        assert resp.status_code == 409

    def test_cannot_revoke_another_users_key(self, auth_client):
        other_key = SDKKeyFactory()  # different owner
        resp = auth_client.post(f"{BASE}/{other_key.id}/revoke/")
        assert resp.status_code == 404


@pytest.mark.django_db
class TestRotateSDKKey:
    def test_rotate_returns_201(self, auth_client, sdk_key):
        resp = auth_client.post(f"{BASE}/{sdk_key.id}/rotate/")
        assert resp.status_code == 201

    def test_rotate_returns_new_full_key(self, auth_client, sdk_key):
        resp = auth_client.post(f"{BASE}/{sdk_key.id}/rotate/")
        data = resp.json()
        assert "key" in data
        assert data["key"].startswith("sdk_srv_")

    def test_rotate_new_key_is_different_from_original(self, auth_client, sdk_key):
        original_prefix = sdk_key.prefix
        resp = auth_client.post(f"{BASE}/{sdk_key.id}/rotate/")
        assert resp.json()["prefix"] != original_prefix

    def test_rotate_deactivates_old_key_in_db(self, auth_client, sdk_key):
        auth_client.post(f"{BASE}/{sdk_key.id}/rotate/")
        sdk_key.refresh_from_db()
        assert sdk_key.is_active is False

    def test_rotate_new_key_is_active(self, auth_client, sdk_key, environment):
        resp = auth_client.post(f"{BASE}/{sdk_key.id}/rotate/")
        new_id = resp.json()["id"]
        new_key = SDKKey.objects.get(pk=new_id)
        assert new_key.is_active is True

    def test_cannot_rotate_another_users_key(self, auth_client):
        other_key = SDKKeyFactory()
        resp = auth_client.post(f"{BASE}/{other_key.id}/rotate/")
        assert resp.status_code == 404
