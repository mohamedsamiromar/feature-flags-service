from rest_framework import serializers

from apps.segments.models import Segment, SegmentRule, SegmentTarget


class SegmentTargetSerializer(serializers.ModelSerializer):
    class Meta:
        model = SegmentTarget
        fields = ["id", "user_key", "excluded", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class SegmentRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = SegmentRule
        fields = ["id", "attribute", "operator", "value", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class SegmentSerializer(serializers.ModelSerializer):
    targets = SegmentTargetSerializer(many=True, read_only=True)
    rules = SegmentRuleSerializer(many=True, read_only=True)

    class Meta:
        model = Segment
        fields = [
            "id", "key", "name", "description",
            "targets", "rules",
            "created_at", "updated_at",
        ]
        # `key` is what targeting rules reference by value, so it is settable
        # on create and frozen afterwards (enforced in SegmentService.update).
        read_only_fields = ["id", "targets", "rules", "created_at", "updated_at"]
