from rest_framework import serializers

from apps.environment.models import Environment, EnvironmentFlag


class EnvironmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Environment
        fields = ["id", "name", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_name(self, value: str) -> str:
        valid = {e.value for e in Environment.EnvironmentName}
        if value not in valid:
            raise serializers.ValidationError(f"Must be one of: {', '.join(sorted(valid))}.")
        return value


class EnvironmentFlagSerializer(serializers.ModelSerializer):
    flag_key = serializers.CharField(source="feature_flag.key", read_only=True)

    class Meta:
        model = EnvironmentFlag
        fields = ["id", "flag_key", "feature_flag", "is_enabled", "rollout_percentage", "updated_at"]
        read_only_fields = ["id", "flag_key", "feature_flag", "updated_at"]
