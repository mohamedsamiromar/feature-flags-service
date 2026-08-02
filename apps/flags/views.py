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
from apps.organizations.queries import ProjectQuery

_service = FlagService()


class FeatureFlagViewSet(viewsets.ModelViewSet):
    """Flags are addressed under their project: /projects/{project_key}/flags/.

    Membership in the project's organization is required for reads; a MEMBER+
    role is required for writes (enforced in FlagService)."""

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = FeatureFlagSerializer
    lookup_field = "key"

    @property
    def project_key(self):
        return self.kwargs["project_key"]

    def get_queryset(self):
        include_archived = self.request.query_params.get("include_archived") == "true"
        # Resolve (and membership-check) the project; 404 if the caller is not a member.
        project = ProjectQuery.get_for_member(self.project_key, self.request.user)
        return FlagQuery.list_for_project(project, include_archived=include_archived)

    def perform_create(self, serializer):
        serializer.instance = _service.create_flag(
            project_key=self.project_key, user=self.request.user, **serializer.validated_data
        )

    def update(self, request, *args, **kwargs):
        partial = kwargs.pop("partial", False)
        serializer = self.get_serializer(data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        flag = _service.update_flag(
            project_key=self.project_key,
            key=kwargs[self.lookup_field],
            user=request.user,
            **serializer.validated_data,
        )
        return Response(FeatureFlagSerializer(flag).data)

    def destroy(self, request, *args, **kwargs):
        _service.delete_flag(
            project_key=self.project_key, key=kwargs[self.lookup_field], user=request.user
        )
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="archive")
    def archive(self, request, key=None, **kwargs):
        flag = _service.archive_flag(project_key=self.project_key, key=key, user=request.user)
        return Response(FeatureFlagSerializer(flag).data)

    @action(detail=True, methods=["post"], url_path="unarchive")
    def unarchive(self, request, key=None, **kwargs):
        flag = _service.unarchive_flag(project_key=self.project_key, key=key, user=request.user)
        return Response(FeatureFlagSerializer(flag).data)

    @action(detail=True, methods=["post"], url_path="toggle")
    def toggle(self, request, key=None, **kwargs):
        """Flip a flag's per-environment kill switch. Body: {"environment": name}."""
        env_flag = _service.toggle_environment(
            project_key=self.project_key,
            key=key,
            user=request.user,
            env_name=request.data.get("environment"),
        )
        return Response(EnvironmentFlagSerializer(env_flag).data)

    # ------------------------------------------------------------------
    # Variation endpoints
    # ------------------------------------------------------------------

    @action(detail=True, methods=["get", "post"], url_path="variations")
    def variations(self, request, key=None, **kwargs):
        if request.method == "GET":
            qs = _service.list_variations(project_key=self.project_key, key=key, user=request.user)
            return Response(VariationSerializer(qs, many=True).data)

        serializer = VariationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        variation = _service.create_variation(
            project_key=self.project_key, key=key, user=request.user, **serializer.validated_data
        )
        return Response(VariationSerializer(variation).data, status=status.HTTP_201_CREATED)

    @action(
        detail=True,
        methods=["patch", "delete"],
        url_path=r"variations/(?P<variation_id>[^/.]+)",
    )
    def variation_detail(self, request, key=None, variation_id=None, **kwargs):
        if request.method == "DELETE":
            _service.delete_variation(
                project_key=self.project_key, key=key, user=request.user, variation_id=variation_id
            )
            return Response(status=status.HTTP_204_NO_CONTENT)

        serializer = VariationSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated = _service.update_variation(
            project_key=self.project_key,
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
    def versions(self, request, key=None, **kwargs):
        qs = _service.list_versions(project_key=self.project_key, key=key, user=request.user)
        return Response(FlagVersionSerializer(qs, many=True).data)

    @action(
        detail=True,
        methods=["get"],
        url_path=r"versions/(?P<version_no>[0-9]+)",
    )
    def version_detail(self, request, key=None, version_no=None, **kwargs):
        version = _service.get_version(
            project_key=self.project_key, key=key, user=request.user, version_no=version_no
        )
        return Response(FlagVersionSerializer(version).data)

    @action(
        detail=True,
        methods=["post"],
        url_path=r"versions/(?P<version_no>[0-9]+)/rollback",
    )
    def version_rollback(self, request, key=None, version_no=None, **kwargs):
        flag = _service.rollback(
            project_key=self.project_key, key=key, user=request.user, version_no=int(version_no)
        )
        return Response(FeatureFlagSerializer(flag).data)
