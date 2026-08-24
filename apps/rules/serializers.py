from rest_framework import serializers

from apps.rules.models import Rule


class RuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Rule
        fields = [
            "id", "flag", "attribute", "operator",
            "value", "priority", "rollout_percentage",
            "serve_variation",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
        # Segment rules carry no attribute; whether a blank one is acceptable
        # depends on `operator`, which is a cross-field business rule and so
        # lives in RuleService, not here.
        extra_kwargs = {"attribute": {"required": False, "allow_blank": True}}

    def validate_rollout_percentage(self, value: int) -> int:
        if not (0 <= value <= 100):
            raise serializers.ValidationError(
                "rollout_percentage must be between 0 and 100."
            )
        return value

    # Cross-user flag ownership is enforced in RuleService (a business rule that
    # also needs the request user); the serializer only (de)serializes and
    # validates field shape.
