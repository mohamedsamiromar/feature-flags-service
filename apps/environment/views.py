from rest_framework import mixins, permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.environment.queries import EnvironmentQuery
from apps.environment.serializers import (
    EnvironmentFlagSerializer,
    EnvironmentSerializer,
    SecureModeSerializer,
    SecureModeUpdateSerializer,
)
from apps.environment.services import (
    EnvironmentFlagService,
    EnvironmentService,
    SecureModeService,
)
from apps.organizations.queries import ProjectQuery
from apps.organizations.services import AccessService

_env_service = EnvironmentService()
_env_flag_service = EnvironmentFlagService()
_secure_mode = SecureModeService()


class EnvironmentViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    Environments live under their project:
    GET    /api/v1/projects/{project_key}/environments/            — list
    POST   /api/v1/projects/{project_key}/environments/            — create (MEMBER+)
    GET    /api/v1/projects/{project_key}/environments/{id}/       — detail
    DELETE /api/v1/projects/{project_key}/environments/{id}/       — delete (MEMBER+)
    GET    /api/v1/projects/{project_key}/environments/{id}/flags/ — per-env flag states
    PATCH  .../environments/{id}/flags/{flag_id}/                  — update flag state (MEMBER+)
    GET    .../environments/{id}/secure-mode/                      — state + secret (MEMBER+)
    PATCH  .../environments/{id}/secure-mode/                      — {"enabled": bool} (MEMBER+)
    POST   .../environments/{id}/secure-mode/rotate/               — new secret (MEMBER+)

    Every secure-mode call needs MEMBER+, reads included: the response carries
    the secret, and a viewer holding it could vouch for any user id.
    """

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = EnvironmentSerializer

    def _project(self, write: bool = False):
        project = ProjectQuery.get_for_member(self.kwargs["project_key"], self.request.user)
        if write:
            AccessService.assert_can_write(self.request.user, project)
        return project

    def get_queryset(self):
        return EnvironmentQuery.list_for_project(self._project())

    def perform_create(self, serializer):
        serializer.instance = _env_service.create(
            project=self._project(write=True), **serializer.validated_data
        )

    def perform_destroy(self, instance):
        self._project(write=True)
        _env_service.delete(instance)

    @action(detail=True, methods=["get"], url_path="flags")
    def flags(self, request, pk=None, **kwargs):
        qs = _env_service.list_flags(self.get_object())
        return Response(EnvironmentFlagSerializer(qs, many=True).data)

    @action(detail=True, methods=["patch"], url_path=r"flags/(?P<flag_id>[^/.]+)")
    def update_flag(self, request, pk=None, flag_id=None, **kwargs):
        self._project(write=True)
        environment = self.get_object()
        serializer = EnvironmentFlagSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated = _env_flag_service.update_state_for_env(
            environment, flag_id, serializer.validated_data
        )
        return Response(EnvironmentFlagSerializer(updated).data)

    @action(detail=True, methods=["get", "patch"], url_path="secure-mode")
    def secure_mode(self, request, pk=None, **kwargs):
        self._project(write=True)
        environment = self.get_object()
        if request.method == "PATCH":
            serializer = SecureModeUpdateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            environment = _secure_mode.set_enabled(
                environment, request.user, serializer.validated_data["enabled"]
            )
        return Response(SecureModeSerializer(_secure_mode.state(environment)).data)

    @action(detail=True, methods=["post"], url_path="secure-mode/rotate")
    def rotate_secure_mode_secret(self, request, pk=None, **kwargs):
        self._project(write=True)
        environment = _secure_mode.rotate(self.get_object(), request.user)
        return Response(SecureModeSerializer(_secure_mode.state(environment)).data)
