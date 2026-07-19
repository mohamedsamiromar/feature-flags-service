from rest_framework import mixins, permissions, viewsets

from apps.audit.queries import AuditQuery
from apps.audit.serializers import AuditLogSerializer


class AuditLogViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Read-only. Users can only see their own audit entries."""

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = AuditLogSerializer

    def get_queryset(self):
        return AuditQuery.list_for_user(self.request.user)
