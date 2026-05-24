from rest_framework import serializers


class SDKEvaluateRequestSerializer(serializers.Serializer):
    flag_key = serializers.CharField()
    # Arbitrary key-value context about the end user (country, plan, user_id…)
    user_context = serializers.DictField(child=serializers.CharField(), default=dict)


class SDKEvaluateResponseSerializer(serializers.Serializer):
    flag_key = serializers.CharField()
    result = serializers.BooleanField()
    environment = serializers.CharField()
