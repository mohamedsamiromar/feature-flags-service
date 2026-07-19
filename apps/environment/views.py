from rest_framework import mixins, permissions, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.environment.queries import EnvironmentQuery
from apps.environment.serializers import EnvironmentFlagSerializer, EnvironmentSerializer
from apps.environment.services import EnvironmentFlagService, EnvironmentService

_env_service = EnvironmentService()
_env_flag_service = EnvironmentFlagService()


class EnvironmentViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    GET    /api/v1/environments/          — list the authenticated user's environments
    POST   /api/v1/environments/          — create an environment
    GET    /api/v1/environments/{id}/     — detail
    DELETE /api/v1/environments/{id}/     — delete (cascades SDK keys + env flags)
    GET    /api/v1/environments/{id}/flags/ — list per-environment flag states
    PATCH  /api/v1/environments/{id}/flags/{flag_id}/ — update flag state for env
    """

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = EnvironmentSerializer

    def get_queryset(self):
        return EnvironmentQuery.list_for_owner(self.request.user)

    def perform_create(self, serializer):
        serializer.instance = _env_service.create(
            user=self.request.user, **serializer.validated_data
        )

    def perform_destroy(self, instance):
        _env_service.delete(instance)

    @action(detail=True, methods=["get"], url_path="flags")
    def flags(self, request, pk=None):
        qs = _env_service.list_flags(self.get_object())
        return Response(EnvironmentFlagSerializer(qs, many=True).data)

    @action(detail=True, methods=["patch"], url_path=r"flags/(?P<flag_id>[^/.]+)")
    def update_flag(self, request, pk=None, flag_id=None):
        serializer = EnvironmentFlagSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated = _env_flag_service.update_state_for_env(
            self.get_object(), flag_id, serializer.validated_data
        )
        return Response(EnvironmentFlagSerializer(updated).data)
