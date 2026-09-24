"""
Refresh tokens can be revoked.

Rotation hands out a new refresh token on every refresh. Without the blacklist
the old one stays valid for its full lifetime (7 days by default), so rotation
bought nothing: a leaked token keeps minting access tokens no matter how often
the real client refreshes. And with no logout, a user had no way to kill one.
"""

import pytest

from conftest import UserFactory

TOKEN = "/api/v1/auth/token/"
REFRESH = "/api/v1/auth/token/refresh/"
LOGOUT = "/api/v1/auth/token/blacklist/"


@pytest.fixture
def refresh_token(api_client, db):
    user = UserFactory()
    # UserFactory skips the post-generation save, so its password never
    # reaches the database. Set one that the login below can use.
    user.set_password("testpass123")
    user.save(update_fields=["password"])
    resp = api_client.post(
        TOKEN, {"username": user.username, "password": "testpass123"}, format="json"
    )
    assert resp.status_code == 200
    return resp.data["refresh"]


@pytest.mark.django_db
class TestRefreshTokenRevocation:
    def test_rotated_token_cannot_be_reused(self, api_client, refresh_token):
        first = api_client.post(REFRESH, {"refresh": refresh_token}, format="json")
        assert first.status_code == 200
        assert first.data["refresh"] != refresh_token

        replay = api_client.post(REFRESH, {"refresh": refresh_token}, format="json")
        assert replay.status_code == 401

    def test_rotated_replacement_still_works(self, api_client, refresh_token):
        rotated = api_client.post(REFRESH, {"refresh": refresh_token}, format="json")
        again = api_client.post(REFRESH, {"refresh": rotated.data["refresh"]}, format="json")
        assert again.status_code == 200

    def test_logout_revokes_the_token(self, api_client, refresh_token):
        resp = api_client.post(LOGOUT, {"refresh": refresh_token}, format="json")
        assert resp.status_code == 200

        after = api_client.post(REFRESH, {"refresh": refresh_token}, format="json")
        assert after.status_code == 401
