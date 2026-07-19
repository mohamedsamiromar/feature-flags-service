from rest_framework import permissions, viewsets

from apps.rules.queries import RuleQuery
from apps.rules.serializers import RuleSerializer
from apps.rules.services import RuleService

_service = RuleService()


class RuleViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = RuleSerializer

    def get_queryset(self):
        return RuleQuery.list_for_owner(self.request.user)

    def perform_create(self, serializer):
        serializer.instance = _service.create(
            self.request.user, serializer.validated_data
        )

    def perform_update(self, serializer):
        serializer.instance = _service.update(
            self.request.user, serializer.instance, serializer.validated_data
        )

    def perform_destroy(self, instance):
        _service.delete(instance)
