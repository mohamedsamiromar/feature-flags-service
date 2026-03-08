from rest_framework import serializers

from apps.audit.models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuditLog
        fields = [
            "id", "action", "entity_type", "entity_id",
            "old_value", "new_value", "created_at",
        ]
        read_only_fields = fields
