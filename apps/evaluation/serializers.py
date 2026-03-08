from rest_framework import serializers

from apps.evaluation.models import EvaluationLog


class EvaluateRequestSerializer(serializers.Serializer):
    flag_key = serializers.CharField()
    user_context = serializers.DictField()


class EvaluateResponseSerializer(serializers.Serializer):
    flag_key = serializers.CharField()
    result = serializers.BooleanField()


class EvaluationLogSerializer(serializers.ModelSerializer):
    flag_key = serializers.CharField(source="flag.key", read_only=True)

    class Meta:
        model = EvaluationLog
        fields = ["id", "flag_key", "result", "evaluated_at"]
        read_only_fields = fields
