from rest_framework import mixins, permissions, viewsets

from apps.evaluation.models import EvaluationLog
from apps.evaluation.serializers import EvaluationLogSerializer


class EvaluationLogViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Read-only log of past evaluations for the authenticated user's flags."""

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = EvaluationLogSerializer

    def get_queryset(self):
        return (
            EvaluationLog.objects
            .filter(flag__owner=self.request.user)
            .select_related("flag")
        )
