from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.environment.models import Environment, EnvironmentFlag
from apps.environment.serializers import EnvironmentFlagSerializer, EnvironmentSerializer
from apps.environment.services import EnvironmentFlagService

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
        return Environment.objects.filter(owner=self.request.user).order_by("name")

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    @action(detail=True, methods=["get"], url_path="flags")
    def flags(self, request, pk=None):
        env = self.get_object()
        qs = (
            EnvironmentFlag.objects
            .filter(environment=env)
            .select_related("feature_flag")
        )
        return Response(EnvironmentFlagSerializer(qs, many=True).data)

    @action(detail=True, methods=["patch"], url_path=r"flags/(?P<flag_id>[^/.]+)")
    def update_flag(self, request, pk=None, flag_id=None):
        env = self.get_object()
        try:
            env_flag = EnvironmentFlag.objects.select_related("feature_flag").get(
                pk=flag_id, environment=env
            )
        except EnvironmentFlag.DoesNotExist:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = EnvironmentFlagSerializer(env_flag, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated = _env_flag_service.update_state(env_flag, serializer.validated_data)
        return Response(EnvironmentFlagSerializer(updated).data)
