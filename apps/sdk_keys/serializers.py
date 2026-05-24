from rest_framework import serializers

from apps.sdk_keys.models import SDKKey


class SDKKeySerializer(serializers.ModelSerializer):
    """Read serializer — never exposes hashed_key or the raw key."""

    environment_name = serializers.CharField(source="environment.name", read_only=True)

    class Meta:
        model = SDKKey
        fields = [
            "id", "name", "prefix", "key_type",
            "environment", "environment_name",
            "is_active", "last_used_at", "created_at", "updated_at",
        ]
        read_only_fields = fields


class SDKKeyCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    key_type = serializers.ChoiceField(choices=SDKKey.KeyType.choices)
    environment = serializers.IntegerField(help_text="Environment ID")

    def validate_environment(self, value: int) -> int:
        user = self.context["request"].user
        from apps.environment.models import Environment
        if not Environment.objects.filter(pk=value, owner=user).exists():
            raise serializers.ValidationError("Environment not found or not owned by you.")
        return value
