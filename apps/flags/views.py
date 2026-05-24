from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.flags.models import FeatureFlag
from apps.flags.serializers import FeatureFlagSerializer
from apps.flags.services import FlagService

_service = FlagService()


class FeatureFlagViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = FeatureFlagSerializer
    lookup_field = "key"

    def get_queryset(self):
        qs = FeatureFlag.objects.filter(owner=self.request.user).prefetch_related("rules")
        if self.request.query_params.get("include_archived") == "true":
            return qs
        return qs.filter(is_archived=False)

    def perform_create(self, serializer):
        instance = _service.create_flag(user=self.request.user, **serializer.validated_data)
        serializer.instance = instance

    def perform_update(self, serializer):
        # Service raises FlagArchivedError (409) if flag.is_archived is True.
        instance = _service.update_flag(
            flag=serializer.instance,
            user=self.request.user,
            **serializer.validated_data,
        )
        serializer.instance = instance

    def perform_destroy(self, instance):
        _service.delete_flag(flag=instance, user=self.request.user)

    def update(self, request, *args, **kwargs):
        # Query across all flags (including archived) so the service can raise
        # FlagArchivedError (409) rather than the queryset producing a 404.
        partial = kwargs.pop("partial", False)
        try:
            flag = FeatureFlag.objects.get(
                key=kwargs.get(self.lookup_field, ""),
                owner=request.user,
            )
        except FeatureFlag.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = self.get_serializer(flag, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response(serializer.data)

    @action(detail=True, methods=["post"], url_path="archive")
    def archive(self, request, key=None):
        try:
            flag = FeatureFlag.objects.get(key=key, owner=request.user)
        except FeatureFlag.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        if flag.is_archived:
            return Response({"detail": "Flag is already archived."}, status=status.HTTP_409_CONFLICT)
        _service.archive_flag(flag=flag, user=request.user)
        return Response(FeatureFlagSerializer(flag).data)

    @action(detail=True, methods=["post"], url_path="unarchive")
    def unarchive(self, request, key=None):
        try:
            flag = FeatureFlag.objects.get(key=key, owner=request.user)
        except FeatureFlag.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        if not flag.is_archived:
            return Response({"detail": "Flag is not archived."}, status=status.HTTP_409_CONFLICT)
        _service.unarchive_flag(flag=flag, user=request.user)
        return Response(FeatureFlagSerializer(flag).data)
