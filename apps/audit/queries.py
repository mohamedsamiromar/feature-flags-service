"""Query layer for the audit app — the only place with ORM access for audit logs."""

from apps.audit.models import AuditLog


class AuditQuery:
    @staticmethod
    def create(**fields) -> AuditLog:
        return AuditLog.objects.create(**fields)

    @staticmethod
    def list_for_user(user):
        return AuditLog.objects.filter(user=user)
