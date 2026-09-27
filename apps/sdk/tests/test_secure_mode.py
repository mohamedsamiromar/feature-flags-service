"""
Secure mode: a client SDK key can only evaluate a user its own backend vouched for.

Client keys ship to browsers. Without secure mode, anyone holding one can
evaluate flags as *any* user id — `{"user_id": "ceo@acme.com"}` — and read
from the results whether that user is individually targeted or in a segment.
With secure mode on, a client-key request must carry
`hash = HMAC-SHA256(environment secret, user_id)`, which only the customer's
backend can compute. Server keys are secret already and are exempt.

What it does not cover: the hash binds `user_id` only. Other context
attributes are still whatever the caller sends.
"""

import hashlib
import hmac
from unittest.mock import patch

import pytest

from apps.audit.models import AuditLog
from apps.environment.models import Environment
from apps.organizations.models import Role
from apps.sdk_keys.models import SDKKey
from conftest import (
    EnvironmentFactory,
    EnvironmentFlagFactory,
    FeatureFlagFactory,
    MembershipFactory,
    SDKKeyFactory,
    UserFactory,
)

EVALUATE = "/api/v1/sdk/evaluate/"
BOOTSTRAP = "/api/v1/sdk/flags/evaluate/"
ME = {"user_id": "u-me"}
CEO = {"user_id": "ceo@acme.com"}


def _hash(secret: str, user_id: str) -> str:
    """What the customer's backend computes. Spelled out here, independently
    of the server's implementation, so the test pins the documented scheme."""
    return hmac.new(secret.encode(), user_id.encode(), hashlib.sha256).hexdigest()


@pytest.fixture
def env(project):
    environment = EnvironmentFactory(project=project)
    flag = FeatureFlagFactory(project=project, key="banner", client_side_available=True)
    EnvironmentFlagFactory(feature_flag=flag, environment=environment)
    return environment


@pytest.fixture
def client_key(env):
    return SDKKeyFactory(environment=env, key_type=SDKKey.KeyType.CLIENT)


@pytest.fixture
def server_key(env):
    return SDKKeyFactory(environment=env, key_type=SDKKey.KeyType.SERVER)


@pytest.fixture
def secure_url(env_base, env):
    return f"{env_base}/{env.id}/secure-mode/"


@pytest.fixture
def secret(auth_client, secure_url):
    """Turn secure mode on through the API and return the environment secret."""
    resp = auth_client.patch(secure_url, {"enabled": True}, format="json")
    assert resp.status_code == 200, resp.content
    return resp.data["secret"]


def _call(api_client, sdk_key, context, hash_=None, url=EVALUATE):
    body = {"user_context": context}
    if url == EVALUATE:
        body["flag_key"] = "banner"
    if hash_ is not None:
        body["hash"] = hash_
    with patch("apps.sdk.views.log_evaluation.delay"):
        return api_client.post(url, body, format="json", HTTP_X_SDK_KEY=sdk_key._full_key)


@pytest.mark.django_db
class TestSecureModeOff:
    def test_client_key_needs_no_hash(self, api_client, client_key):
        assert _call(api_client, client_key, ME).status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("url", [EVALUATE, BOOTSTRAP])
class TestSecureModeOn:
    def test_missing_hash_is_403(self, api_client, client_key, secret, url):
        assert _call(api_client, client_key, ME, url=url).status_code == 403

    def test_garbage_hash_is_403(self, api_client, client_key, secret, url):
        assert _call(api_client, client_key, ME, "00" * 32, url=url).status_code == 403

    def test_valid_hash_is_accepted(self, api_client, client_key, secret, url):
        resp = _call(api_client, client_key, ME, _hash(secret, ME["user_id"]), url=url)
        assert resp.status_code == 200

    def test_my_hash_cannot_evaluate_someone_else(self, api_client, client_key, secret, url):
        """The probe secure mode exists to stop: a valid hash for my own id,
        reused to evaluate as another user."""
        resp = _call(api_client, client_key, CEO, _hash(secret, ME["user_id"]), url=url)
        assert resp.status_code == 403

    def test_server_key_is_exempt(self, api_client, server_key, secret, url):
        assert _call(api_client, server_key, CEO, url=url).status_code == 200

    def test_rotation_invalidates_old_hashes(
        self, api_client, auth_client, client_key, secret, secure_url, url
    ):
        old_hash = _hash(secret, ME["user_id"])
        rotated = auth_client.post(f"{secure_url}rotate/")
        assert rotated.status_code == 200
        new_secret = rotated.data["secret"]
        assert new_secret != secret

        assert _call(api_client, client_key, ME, old_hash, url=url).status_code == 403
        assert _call(
            api_client, client_key, ME, _hash(new_secret, ME["user_id"]), url=url
        ).status_code == 200

    def test_turning_it_off_restores_hashless_access(
        self, api_client, auth_client, client_key, secret, secure_url, url
    ):
        auth_client.patch(secure_url, {"enabled": False}, format="json")
        assert _call(api_client, client_key, ME, url=url).status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("url", [EVALUATE, BOOTSTRAP])
class TestSecureModeFailsClosed:
    def test_enabled_without_a_secret_rejects_every_client_request(
        self, api_client, env, client_key, url
    ):
        """No API path produces this state (enabling generates a secret), but a
        shell or data migration could. HMAC with an empty key is computable by
        anyone, so accepting it would make secure mode a no-op while claiming
        to be on. Uncertainty fails closed."""
        Environment.objects.filter(pk=env.pk).update(secure_mode=True, secure_mode_secret="")

        resp = _call(api_client, client_key, CEO, _hash("", CEO["user_id"]), url=url)

        assert resp.status_code == 403

    def test_server_key_is_still_exempt(self, api_client, env, server_key, url):
        Environment.objects.filter(pk=env.pk).update(secure_mode=True, secure_mode_secret="")
        assert _call(api_client, server_key, CEO, url=url).status_code == 200


@pytest.mark.django_db
class TestSecretManagement:
    def test_off_by_default_with_no_secret(self, auth_client, secure_url):
        resp = auth_client.get(secure_url)
        assert resp.status_code == 200
        assert resp.data == {"enabled": False, "secret": None}

    def test_enabling_generates_a_secret(self, auth_client, secure_url):
        resp = auth_client.patch(secure_url, {"enabled": True}, format="json")
        assert resp.data["enabled"] is True
        assert len(resp.data["secret"]) >= 32

    def test_re_enabling_keeps_the_secret(self, auth_client, secure_url, secret):
        """Toggling must not silently invalidate every hash a backend issued."""
        auth_client.patch(secure_url, {"enabled": False}, format="json")
        resp = auth_client.patch(secure_url, {"enabled": True}, format="json")
        assert resp.data["secret"] == secret

    def test_viewer_cannot_read_or_change_it(self, env, secure_url, secret):
        from rest_framework.test import APIClient
        viewer = UserFactory()
        MembershipFactory(organization=env.project.organization, user=viewer, role=Role.VIEWER)
        client = APIClient()
        client.force_authenticate(viewer)

        assert client.get(secure_url).status_code == 403
        assert client.patch(secure_url, {"enabled": False}, format="json").status_code == 403
        assert client.post(f"{secure_url}rotate/").status_code == 403

    def test_non_member_gets_404(self, secure_url):
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(UserFactory())
        assert client.get(secure_url).status_code == 404

    def test_secret_never_in_environment_responses(self, auth_client, env_base, env, secret):
        listed = auth_client.get(f"{env_base}/")
        detail = auth_client.get(f"{env_base}/{env.id}/")
        assert secret not in repr(listed.json())
        assert secret not in repr(detail.json())

    def test_secret_never_in_the_audit_trail(self, auth_client, secure_url, secret):
        auth_client.post(f"{secure_url}rotate/")
        new_secret = Environment.objects.get().secure_mode_secret
        logs = AuditLog.objects.filter(entity_type="environment")
        assert logs.exists()
        for log in logs:
            assert secret not in repr((log.old_value, log.new_value))
            assert new_secret not in repr((log.old_value, log.new_value))
