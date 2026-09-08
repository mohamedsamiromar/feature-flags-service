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


class SDKConfigResponseSerializer(serializers.Serializer):
    """The config-download wire format. See SDK_CONFIG_SPEC.md §3.

    Deliberately loose below the top level: `flags` and `segments` are passed
    through as JSON. The shape is produced by `FlagEvaluationService.config_for`
    from the engine's own evaluation payloads, and re-declaring every nested
    field here would create a second definition of the contract that could
    silently disagree with the first. `format_version` is what an SDK checks.
    """

    format_version = serializers.IntegerField()
    environment = serializers.CharField()
    config_version = serializers.IntegerField()
    segments = serializers.JSONField()
    flags = serializers.JSONField()
