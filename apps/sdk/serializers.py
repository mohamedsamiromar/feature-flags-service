from rest_framework import serializers


class SDKEvaluateRequestSerializer(serializers.Serializer):
    flag_key = serializers.CharField()
    user_context = serializers.DictField(default=dict)


class SDKEvaluateResponseSerializer(serializers.Serializer):
    flag_key = serializers.CharField()
    result = serializers.JSONField()
    result_type = serializers.CharField()
    environment = serializers.CharField()


class SDKEvaluateAllRequestSerializer(serializers.Serializer):
    user_context = serializers.DictField(default=dict)


class SDKFlagResultSerializer(serializers.Serializer):
    result = serializers.JSONField()
    result_type = serializers.CharField()
    # Null for a flag with no variations configured (legacy boolean flags).
    variation_id = serializers.IntegerField(allow_null=True)


class SDKEvaluateAllResponseSerializer(serializers.Serializer):
    environment = serializers.CharField()
    # Keyed by flag key: an SDK looks a flag up by name, it does not scan a list.
    flags = serializers.DictField(child=SDKFlagResultSerializer())
