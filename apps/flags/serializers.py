from rest_framework import serializers

from apps.flags.models import (
    FeatureFlag,
    FlagPrerequisite,
    FlagTarget,
    FlagVersion,
    Variation,
)


class FlagVersionSerializer(serializers.ModelSerializer):
    changed_by = serializers.StringRelatedField()

    class Meta:
        model = FlagVersion
        fields = [
            "version_no",
            "change_action",
            "source_version_no",
            "snapshot",
            "changed_by",
            "created_at",
        ]
        read_only_fields = fields


class VariationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Variation
        fields = ["id", "name", "value_type", "value", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class FlagTargetSerializer(serializers.ModelSerializer):
    """Read/write shape for an individual user target.

    `variation` is a plain pk on write; the service checks it belongs to this
    flag (cross-entity checks never live in a serializer). `variation_name` is
    echoed back so a dashboard can render the target without a second call.
    """

    variation_name = serializers.CharField(source="variation.name", read_only=True)

    class Meta:
        model = FlagTarget
        fields = ["id", "user_key", "variation", "variation_name", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class FlagPrerequisiteSerializer(serializers.ModelSerializer):
    """Read shape plus the two write fields.

    Written by key + variation id rather than by pk, so a caller says "gate this
    behind new-cart serving `on`" without first looking up internal ids. The
    cross-entity checks (same project, variation belongs to the prerequisite, no
    cycle) live in FlagService.
    """

    prerequisite_key = serializers.CharField(source="prerequisite_flag.key")
    variation_id = serializers.IntegerField(source="required_variation_id")
    variation_name = serializers.CharField(source="required_variation.name", read_only=True)

    class Meta:
        model = FlagPrerequisite
        fields = ["id", "prerequisite_key", "variation_id", "variation_name",
                  "created_at", "updated_at"]
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
