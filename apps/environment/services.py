import hashlib
import hmac
import secrets

from apps.audit.services import AuditService
from apps.core.errors import APIError, Error
from apps.environment.models import Environment, EnvironmentFlag
from apps.environment.queries import EnvironmentFlagQuery, EnvironmentQuery


class EnvironmentService:
    """Business logic for environments; delegates persistence to the query layer."""

    def create(self, project, **validated_data) -> Environment:
        return EnvironmentQuery.create(project=project, **validated_data)

    def delete(self, environment: Environment) -> None:
        EnvironmentQuery.delete(environment)

    def list_flags(self, environment):
        return EnvironmentFlagQuery.list_for_env(environment)


class EnvironmentFlagService:
    """Owns all mutations to EnvironmentFlag, including cache invalidation."""

    def update_state_for_env(self, environment, flag_id, validated_data: dict) -> EnvironmentFlag:
        env_flag = EnvironmentFlagQuery.get_for_env(environment, flag_id)
        return self.update_state(env_flag, validated_data)

    def update_state(self, env_flag: EnvironmentFlag, validated_data: dict) -> EnvironmentFlag:
        for attr, value in validated_data.items():
            setattr(env_flag, attr, value)
        EnvironmentFlagQuery.save(env_flag)

        self._invalidate_cache(env_flag)
        return env_flag

    def toggle(self, env_flag: EnvironmentFlag, user) -> EnvironmentFlag:
        """Flip the per-environment kill switch and record the change."""
        old_snapshot = AuditService.snapshot(env_flag)
        env_flag.is_enabled = not env_flag.is_enabled
        EnvironmentFlagQuery.save(env_flag, update_fields=["is_enabled", "updated_at"])

        self._invalidate_cache(env_flag)
        AuditService.log(
            user=user,
            action=AuditService.TOGGLE,
            entity=env_flag,
            old_value=old_snapshot,
            new_value=AuditService.snapshot(env_flag),
        )
        return env_flag

    @staticmethod
    def _invalidate_cache(env_flag: EnvironmentFlag) -> None:
        """Evict this env's copy of the flag and advance the env's config version.

        Goes through `FlagEvaluationService.invalidate_cache` rather than
        formatting the cache key here. Two hand-written copies of that format is
        how the pre-tenancy version of this code ended up evicting one key while
        evaluation read another.
        """
        from apps.evaluation.services import FlagEvaluationService

        env = env_flag.environment
        FlagEvaluationService.invalidate_cache(
            project_id=env.project_id,
            flag_key=env_flag.feature_flag.key,
            env_id=env.id,
        )
        EnvironmentQuery.bump_config_versions([env.id])


class SecureModeService:
    """Secure mode for client SDK keys.

    A client key ships to browsers, so without this anyone holding one can
    evaluate flags as any user id and learn from the results who is
    individually targeted or in a segment. In secure mode a client-key request
    must carry ``HMAC-SHA256(secure_mode_secret, user_id)`` as hex — computed by
    the customer's own backend, which is the only party holding the secret.
    Server keys are secret already and are never asked for one.

    The hash binds ``user_id`` only, as LaunchDarkly's does. Other context
    attributes are still whatever the caller sends; rules on them can still be
    probed with a client key.
    """

    @staticmethod
    def state(environment) -> dict:
        return {
            "enabled": environment.secure_mode,
            "secret": environment.secure_mode_secret or None,
        }

    def set_enabled(self, environment, user, enabled: bool):
        old_snapshot = AuditService.snapshot(environment)
        environment.secure_mode = enabled
        # Generated on first enable and then kept: re-enabling must not
        # silently invalidate every hash a backend has already issued.
        if enabled and not environment.secure_mode_secret:
            environment.secure_mode_secret = self._new_secret()
        EnvironmentQuery.save(
            environment,
            update_fields=["secure_mode", "secure_mode_secret", "updated_at"],
        )
        AuditService.log(
            user=user,
            action=AuditService.UPDATE,
            entity=environment,
            old_value=old_snapshot,
            new_value=AuditService.snapshot(environment),
        )
        return environment

    def rotate(self, environment, user):
        """Replace the secret. Every hash issued under the old one stops working
        at once — the point of rotating after a leak."""
        old_snapshot = AuditService.snapshot(environment)
        environment.secure_mode_secret = self._new_secret()
        EnvironmentQuery.save(environment, update_fields=["secure_mode_secret", "updated_at"])
        AuditService.log(
            user=user,
            action=AuditService.ROTATE,
            entity=environment,
            old_value=old_snapshot,
            new_value=AuditService.snapshot(environment),
        )
        return environment

    @staticmethod
    def assert_vouched(sdk_key, user_context: dict, supplied_hash: str) -> None:
        """Raise 403 unless this request may evaluate `user_context`.

        A no-op for server keys and for environments not in secure mode. The
        environment comes from the key's `select_related`, so this costs no
        query on the SDK hot path.
        """
        environment = sdk_key.environment
        if not sdk_key.client_side_only or not environment.secure_mode:
            return
        expected = hmac.new(
            environment.secure_mode_secret.encode(),
            str(user_context.get("user_id", "")).encode(),
            hashlib.sha256,
        ).hexdigest()
        # Constant-time, on bytes: compare_digest refuses non-ASCII str, and a
        # caller-supplied hash can contain anything.
        if not supplied_hash or not hmac.compare_digest(
            expected.encode(), supplied_hash.encode()
        ):
            raise APIError(Error.SECURE_MODE_HASH_INVALID)

    @staticmethod
    def _new_secret() -> str:
        return secrets.token_hex(32)
