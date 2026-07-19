from django.core.cache import cache

from apps.audit.services import AuditService
from apps.environment.models import Environment, EnvironmentFlag
from apps.environment.queries import EnvironmentFlagQuery, EnvironmentQuery


class EnvironmentService:
    """Business logic for environments; delegates persistence to the query layer."""

    def create(self, user, **validated_data) -> Environment:
        return EnvironmentQuery.create(owner=user, **validated_data)

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
        env = env_flag.environment
        cache.delete(f"flags:{env.owner_id}:{env.id}:{env_flag.feature_flag.key}")
