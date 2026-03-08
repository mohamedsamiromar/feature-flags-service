from rest_framework import serializers

from apps.flags.models import FeatureFlag


class FeatureFlagSerializer(serializers.ModelSerializer):
    class Meta:
        model = FeatureFlag
        fields = [
            "id", "name", "key", "description",
            "is_enabled", "rollout_percentage",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_rollout_percentage(self, value: int) -> int:
        """
        Enforce the 0–100 range at the API boundary so callers receive a clean
        400 error rather than a DB IntegrityError or a silent logical bug
        (e.g. percentage=150 silently enables every user because
        hash % 100 < 150 is always True).
        """
        if not (0 <= value <= 100):
            raise serializers.ValidationError(
                "rollout_percentage must be between 0 and 100."
            )
        return value
