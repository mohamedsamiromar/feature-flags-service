from rest_framework import serializers


class SDKEvaluateRequestSerializer(serializers.Serializer):
    flag_key = serializers.CharField()
    user_context = serializers.DictField(default=dict)


class SDKEvaluateResponseSerializer(serializers.Serializer):
    flag_key = serializers.CharField()
    result = serializers.JSONField()
    result_type = serializers.CharField()
    environment = serializers.CharField()
