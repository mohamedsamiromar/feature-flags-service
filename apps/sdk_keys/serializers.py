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
    # Environment ownership is enforced in SDKKeyService.create_key (business
    # rule + DB access belong in the service/query layers, not the serializer).
