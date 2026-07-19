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

    # Cross-user flag ownership is enforced in RuleService (a business rule that
    # also needs the request user); the serializer only (de)serializes and
    # validates field shape.
