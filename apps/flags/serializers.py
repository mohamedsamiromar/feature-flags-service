from rest_framework import serializers

from apps.flags.models import FeatureFlag, Variation


class VariationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Variation
        fields = ["id", "name", "value_type", "value", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class FeatureFlagSerializer(serializers.ModelSerializer):
    variations = VariationSerializer(many=True, read_only=True)

    class Meta:
        model = FeatureFlag
        fields = [
            "id", "name", "key", "description",
            "flag_type",
            "is_enabled", "rollout_percentage",
            "off_variation", "fallthrough_variation",
            "is_archived",
            "variations",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "is_archived", "created_at", "updated_at", "variations"]

    def validate_rollout_percentage(self, value: int) -> int:
        if not (0 <= value <= 100):
            raise serializers.ValidationError(
                "rollout_percentage must be between 0 and 100."
            )
        return value

    def validate(self, attrs):
        flag = self.instance
        for field in ("off_variation", "fallthrough_variation"):
            variation = attrs.get(field)
            if variation is not None and flag is not None:
                if variation.flag_id != flag.id:
                    raise serializers.ValidationError(
                        {field: "Variation does not belong to this flag."}
                    )
        return attrs
