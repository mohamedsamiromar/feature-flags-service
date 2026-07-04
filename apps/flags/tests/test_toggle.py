"""
Dedicated flag toggle endpoint — POST /flags/{key}/toggle/.

Toggles the per-environment kill switch (EnvironmentFlag.is_enabled) for the
environment named in the request body. Every test hits the real HTTP stack.
"""

import pytest

from apps.audit.models import AuditLog
from apps.environment.models import EnvironmentFlag

from conftest import (
    EnvironmentFactory,
    EnvironmentFlagFactory,
    FeatureFlagFactory,
)

BASE = "/api/v1/flags"


@pytest.mark.django_db
class TestToggleEndpoint:
    def test_toggle_flips_enabled_state(self, auth_client, flag, environment):
        env_flag = EnvironmentFlagFactory(
            feature_flag=flag, environment=environment, is_enabled=True
        )
        resp = auth_client.post(
            f"{BASE}/{flag.key}/toggle/",
            {"environment": environment.name},
            format="json",
        )
        assert resp.status_code == 200
        assert resp.json()["is_enabled"] is False
        env_flag.refresh_from_db()
        assert env_flag.is_enabled is False

    def test_toggle_turns_disabled_flag_on(self, auth_client, flag, environment):
        EnvironmentFlagFactory(
            feature_flag=flag, environment=environment, is_enabled=False
        )
        resp = auth_client.post(
            f"{BASE}/{flag.key}/toggle/",
            {"environment": environment.name},
            format="json",
        )
        assert resp.json()["is_enabled"] is True

    def test_toggle_creates_env_flag_on_first_call(self, auth_client, flag, environment):
        assert not EnvironmentFlag.objects.filter(
            feature_flag=flag, environment=environment
        ).exists()
        resp = auth_client.post(
            f"{BASE}/{flag.key}/toggle/",
            {"environment": environment.name},
            format="json",
        )
        assert resp.status_code == 200
        # Created defaulting to off, then flipped on.
        assert resp.json()["is_enabled"] is True
        assert EnvironmentFlag.objects.filter(
            feature_flag=flag, environment=environment
        ).count() == 1

    def test_toggle_writes_audit_log(self, auth_client, flag, environment):
        env_flag = EnvironmentFlagFactory(
            feature_flag=flag, environment=environment
        )
        auth_client.post(
            f"{BASE}/{flag.key}/toggle/",
            {"environment": environment.name},
            format="json",
        )
        assert AuditLog.objects.filter(
            entity_id=str(env_flag.pk), action="toggle"
        ).exists()

    def test_toggle_missing_environment_returns_400(self, auth_client, flag):
        resp = auth_client.post(f"{BASE}/{flag.key}/toggle/", {}, format="json")
        assert resp.status_code == 400

    def test_toggle_unknown_environment_returns_404(self, auth_client, flag):
        resp = auth_client.post(
            f"{BASE}/{flag.key}/toggle/",
            {"environment": "staging"},
            format="json",
        )
        assert resp.status_code == 404

    def test_toggle_archived_flag_returns_409(self, auth_client, user, environment):
        flag = FeatureFlagFactory(owner=user, is_archived=True)
        resp = auth_client.post(
            f"{BASE}/{flag.key}/toggle/",
            {"environment": environment.name},
            format="json",
        )
        assert resp.status_code == 409

    def test_toggle_nonexistent_flag_returns_404(self, auth_client):
        resp = auth_client.post(
            f"{BASE}/does-not-exist/toggle/",
            {"environment": "production"},
            format="json",
        )
        assert resp.status_code == 404

    def test_toggle_another_users_flag_returns_404(self, auth_client, environment):
        other_flag = FeatureFlagFactory()  # different owner
        resp = auth_client.post(
            f"{BASE}/{other_flag.key}/toggle/",
            {"environment": environment.name},
            format="json",
        )
        assert resp.status_code == 404

    def test_toggle_another_users_environment_returns_404(self, auth_client, flag):
        other_env = EnvironmentFactory()  # different owner, name "production"
        resp = auth_client.post(
            f"{BASE}/{flag.key}/toggle/",
            {"environment": other_env.name},
            format="json",
        )
        assert resp.status_code == 404

    def test_unauthenticated_toggle_returns_401(self, api_client, flag, environment):
        resp = api_client.post(
            f"{BASE}/{flag.key}/toggle/",
            {"environment": environment.name},
            format="json",
        )
        assert resp.status_code == 401
