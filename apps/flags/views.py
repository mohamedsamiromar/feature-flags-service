from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.environment.models import Environment, EnvironmentFlag
from apps.environment.serializers import EnvironmentFlagSerializer
from apps.environment.services import EnvironmentFlagService
from apps.flags.models import FeatureFlag, Variation
from apps.flags.serializers import FeatureFlagSerializer, VariationSerializer
from apps.flags.services import FlagService

_service = FlagService()
_env_flag_service = EnvironmentFlagService()


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

    @action(detail=True, methods=["post"], url_path="toggle")
    def toggle(self, request, key=None):
        """
        Flip a flag's per-environment kill switch in one call.

        Body: {"environment": "production"}. The EnvironmentFlag is created on
        first toggle (defaulting to off, so the first toggle turns it on).
        """
        try:
            flag = FeatureFlag.objects.get(key=key, owner=request.user)
        except FeatureFlag.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        if flag.is_archived:
            return Response(
                {"detail": "Cannot toggle an archived flag."},
                status=status.HTTP_409_CONFLICT,
            )

        env_name = request.data.get("environment")
        if not env_name:
            return Response(
                {"detail": "'environment' is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            env = Environment.objects.get(owner=request.user, name=env_name)
        except Environment.DoesNotExist:
            return Response(
                {"detail": f"Environment '{env_name}' not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        env_flag, _ = EnvironmentFlag.objects.get_or_create(
            feature_flag=flag, environment=env
        )
        updated = _env_flag_service.toggle(env_flag, request.user)
        return Response(EnvironmentFlagSerializer(updated).data)

    # ------------------------------------------------------------------
    # Variation endpoints
    # ------------------------------------------------------------------

    @action(detail=True, methods=["get", "post"], url_path="variations")
    def variations(self, request, key=None):
        flag = self.get_object()

        if request.method == "GET":
            qs = Variation.objects.filter(flag=flag)
            return Response(VariationSerializer(qs, many=True).data)

        serializer = VariationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        variation = _service.create_variation(
            flag=flag,
            user=request.user,
            **serializer.validated_data,
        )
        return Response(VariationSerializer(variation).data, status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=["patch", "delete"],
        url_path=r"variations/(?P<variation_id>[^/.]+)",
    )
    def variation_detail(self, request, key=None, variation_id=None):
        flag = self.get_object()
        try:
            variation = Variation.objects.get(pk=variation_id, flag=flag)
        except Variation.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        if request.method == "DELETE":
            _service.delete_variation(variation=variation, user=request.user)
            return Response(status=status.HTTP_204_NO_CONTENT)

        serializer = VariationSerializer(variation, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated = _service.update_variation(
            variation=variation,
            user=request.user,
            **serializer.validated_data,
        )
        return Response(VariationSerializer(updated).data)
