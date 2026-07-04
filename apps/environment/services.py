from django.core.cache import cache

from apps.audit.services import AuditService
from apps.environment.models import EnvironmentFlag


class EnvironmentFlagService:
    """Owns all mutations to EnvironmentFlag, including cache invalidation."""

    def update_state(self, env_flag: EnvironmentFlag, validated_data: dict) -> EnvironmentFlag:
        for attr, value in validated_data.items():
            setattr(env_flag, attr, value)
        env_flag.save()

        self._invalidate_cache(env_flag)
        return env_flag

    def toggle(self, env_flag: EnvironmentFlag, user) -> EnvironmentFlag:
        """Flip the per-environment kill switch and record the change."""
        old_snapshot = AuditService.snapshot(env_flag)
        env_flag.is_enabled = not env_flag.is_enabled
        env_flag.save(update_fields=["is_enabled", "updated_at"])

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
