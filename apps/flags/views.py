from rest_framework import permissions, viewsets

from apps.flags.models import FeatureFlag
from apps.flags.serializers import FeatureFlagSerializer
from apps.flags.services import FlagService

_service = FlagService()


class FeatureFlagViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = FeatureFlagSerializer
    lookup_field = "key"

    def get_queryset(self):
        # Only the authenticated user's own flags — never global data
        return (
            FeatureFlag.objects
            .filter(owner=self.request.user)
            .prefetch_related("rules")
        )

    def perform_create(self, serializer):
        _service.create_flag(user=self.request.user, **serializer.validated_data)

    def perform_update(self, serializer):
        _service.update_flag(
            flag=self.get_object(),
            user=self.request.user,
            **serializer.validated_data,
        )

    def perform_destroy(self, instance):
        _service.delete_flag(flag=instance, user=self.request.user)
