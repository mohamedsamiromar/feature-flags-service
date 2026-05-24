from django.core.cache import cache

from apps.environment.models import EnvironmentFlag


class EnvironmentFlagService:
    """Owns all mutations to EnvironmentFlag, including cache invalidation."""

    def update_state(self, env_flag: EnvironmentFlag, validated_data: dict) -> EnvironmentFlag:
        for attr, value in validated_data.items():
            setattr(env_flag, attr, value)
        env_flag.save()

        env = env_flag.environment
        cache.delete(f"flags:{env.owner_id}:{env.id}:{env_flag.feature_flag.key}")
        return env_flag
