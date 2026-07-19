from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.environment.serializers import EnvironmentFlagSerializer
from apps.flags.queries import FlagQuery
from apps.flags.serializers import (
    FeatureFlagSerializer,
    FlagVersionSerializer,
    VariationSerializer,
)
from apps.flags.services import FlagService

_service = FlagService()


class FeatureFlagViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = FeatureFlagSerializer
    lookup_field = "key"

    def get_queryset(self):
        include_archived = self.request.query_params.get("include_archived") == "true"
        return FlagQuery.list_for_owner(self.request.user, include_archived=include_archived)

    def perform_create(self, serializer):
        serializer.instance = _service.create_flag(
            user=self.request.user, **serializer.validated_data
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        # Validate the payload against the serializer, then let the service fetch
        # (any archived state) and raise INSTANCE_NOT_FOUND (404) / FLAG_ARCHIVED
        # (409) as appropriate — no lookup or branching in the view.
        serializer = self.get_serializer(data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        flag = _service.update_flag(
            key=kwargs[self.lookup_field],
            user=request.user,
            **serializer.validated_data,
        )
        return Response(FeatureFlagSerializer(flag).data)

    def destroy(self, request, *args, **kwargs):
        _service.delete_flag(key=kwargs[self.lookup_field], user=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="archive")
    def archive(self, request, key=None):
        flag = _service.archive_flag(key=key, user=request.user)
        return Response(FeatureFlagSerializer(flag).data)

    @action(detail=True, methods=["post"], url_path="unarchive")
    def unarchive(self, request, key=None):
        flag = _service.unarchive_flag(key=key, user=request.user)
        return Response(FeatureFlagSerializer(flag).data)

    @action(detail=True, methods=["post"], url_path="toggle")
    def toggle(self, request, key=None):
        """Flip a flag's per-environment kill switch. Body: {"environment": name}."""
        env_flag = _service.toggle_environment(
            key=key, user=request.user, env_name=request.data.get("environment")
        )
        return Response(EnvironmentFlagSerializer(env_flag).data)

    # ------------------------------------------------------------------
    # Variation endpoints
    # ------------------------------------------------------------------

    @action(detail=True, methods=["get", "post"], url_path="variations")
    def variations(self, request, key=None):
        if request.method == "GET":
            qs = _service.list_variations(key=key, user=request.user)
            return Response(VariationSerializer(qs, many=True).data)

        serializer = VariationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        variation = _service.create_variation(
            key=key, user=request.user, **serializer.validated_data
        )
        return Response(VariationSerializer(variation).data, status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=["patch", "delete"],
        url_path=r"variations/(?P<variation_id>[^/.]+)",
    )
    def variation_detail(self, request, key=None, variation_id=None):
        if request.method == "DELETE":
            _service.delete_variation(key=key, user=request.user, variation_id=variation_id)
            return Response(status=status.HTTP_204_NO_CONTENT)

        serializer = VariationSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated = _service.update_variation(
            key=key,
            user=request.user,
            variation_id=variation_id,
            **serializer.validated_data,
        )
        return Response(VariationSerializer(updated).data)

    # ------------------------------------------------------------------
    # Version history & rollback
    # ------------------------------------------------------------------

    @action(detail=True, methods=["get"], url_path="versions")
    def versions(self, request, key=None):
        qs = _service.list_versions(key=key, user=request.user)
        return Response(FlagVersionSerializer(qs, many=True).data)

    @action(
        detail=True,
        methods=["get"],
        url_path=r"versions/(?P<version_no>[0-9]+)",
    )
    def version_detail(self, request, key=None, version_no=None):
        version = _service.get_version(key=key, user=request.user, version_no=version_no)
        return Response(FlagVersionSerializer(version).data)

    @action(
        detail=True,
        methods=["post"],
        url_path=r"versions/(?P<version_no>[0-9]+)/rollback",
    )
    def version_rollback(self, request, key=None, version_no=None):
        flag = _service.rollback(
            key=key, user=request.user, version_no=int(version_no)
        )
        return Response(FeatureFlagSerializer(flag).data)
