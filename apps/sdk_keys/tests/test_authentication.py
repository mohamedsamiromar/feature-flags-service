"""
F-03: SDKKeyAuthentication — unit tests.

The DRF authentication class is tested directly (not through the HTTP stack)
by constructing a minimal request mock and calling authenticate().
"""

import pytest
from django.test import RequestFactory
from rest_framework.exceptions import AuthenticationFailed

from apps.sdk_keys.authentication import SDKKeyAuthentication
from apps.sdk_keys.models import SDKKey
from conftest import SDKKeyFactory, EnvironmentFactory, UserFactory

auth = SDKKeyAuthentication()


def make_request(sdk_key_value=None):
    """Return a Django request with the X-SDK-Key header set (or absent)."""
    factory = RequestFactory()
    request = factory.get("/")
    if sdk_key_value is not None:
        request.META["HTTP_X_SDK_KEY"] = sdk_key_value
    return request


@pytest.mark.django_db
class TestSDKKeyAuthentication:
    def test_missing_header_returns_none(self):
        """No header → pass through to the next authenticator (JWT)."""
        request = make_request()
        assert auth.authenticate(request) is None

    def test_valid_key_returns_user_and_sdk_key(self, sdk_key, environment):
        request = make_request(sdk_key._full_key)
        user, returned_key = auth.authenticate(request)
        assert user == environment.owner
        assert returned_key.pk == sdk_key.pk

    def test_valid_key_authenticate_header(self):
        assert auth.authenticate_header(make_request()) == "X-SDK-Key"

    def test_invalid_key_raises_authentication_failed(self):
        request = make_request("sdk_srv_totally_invalid_key_that_does_not_exist")
        with pytest.raises(AuthenticationFailed):
            auth.authenticate(request)

    def test_revoked_key_raises_authentication_failed(self, sdk_key):
        sdk_key.is_active = False
        sdk_key.save(update_fields=["is_active", "updated_at"])
        request = make_request(sdk_key._full_key)
        with pytest.raises(AuthenticationFailed):
            auth.authenticate(request)

    def test_last_used_at_updated_on_successful_auth(self, sdk_key):
        assert sdk_key.last_used_at is None
        request = make_request(sdk_key._full_key)
        auth.authenticate(request)
        sdk_key.refresh_from_db()
        assert sdk_key.last_used_at is not None

    def test_client_key_also_authenticates(self, environment):
        client_key = SDKKeyFactory(
            environment=environment,
            key_type=SDKKey.KeyType.CLIENT,
        )
        request = make_request(client_key._full_key)
        user, returned_key = auth.authenticate(request)
        assert returned_key.key_type == SDKKey.KeyType.CLIENT
        assert user == environment.owner
