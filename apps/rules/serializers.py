from rest_framework import serializers

from apps.rules.models import Rule


class RuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Rule
        fields = [
            "id", "flag", "attribute", "operator",
            "value", "priority",
            "serve_variation",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
        # Segment rules carry no attribute; whether a blank one is acceptable
        # depends on `operator`, which is a cross-field business rule and so
        # lives in RuleService, not here.
        extra_kwargs = {"attribute": {"required": False, "allow_blank": True}}

    # Cross-user flag ownership is enforced in RuleService (a business rule that
    # also needs the request user); the serializer only (de)serializes and
    # validates field shape.
