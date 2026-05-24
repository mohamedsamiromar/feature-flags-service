from rest_framework import mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.sdk_keys.models import SDKKey
from apps.sdk_keys.serializers import SDKKeyCreateSerializer, SDKKeySerializer
from apps.sdk_keys.services import SDKKeyService

_service = SDKKeyService()


class SDKKeyViewSet(
    mixins.CreateModelMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """
    Manage SDK keys for the authenticated user's environments.

    POST   /api/v1/sdk-keys/            — create (returns full key once)
    GET    /api/v1/sdk-keys/            — list (prefix only, never full key)
    GET    /api/v1/sdk-keys/{id}/       — detail
    POST   /api/v1/sdk-keys/{id}/revoke/  — deactivate
    POST   /api/v1/sdk-keys/{id}/rotate/  — deactivate + issue replacement
    """

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = SDKKeySerializer

    def get_queryset(self):
        return (
            SDKKey.objects
            .filter(environment__owner=self.request.user)
            .select_related("environment")
            .order_by("-created_at")
        )

    def create(self, request, *args, **kwargs):
        serializer = SDKKeyCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        sdk_key, full_key = _service.create_key(
            user=request.user,
            environment_id=serializer.validated_data["environment"],
            name=serializer.validated_data["name"],
            key_type=serializer.validated_data["key_type"],
        )

        data = SDKKeySerializer(sdk_key).data
        # Surface full key exactly once — it is not stored and cannot be recovered.
        data["key"] = full_key
        return Response(data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def revoke(self, request, pk=None):
        sdk_key = self.get_object()
        if not sdk_key.is_active:
            return Response({"detail": "Key is already revoked."}, status=status.HTTP_409_CONFLICT)
        _service.revoke(sdk_key=sdk_key, user=request.user)
        return Response(SDKKeySerializer(sdk_key).data)

    @action(detail=True, methods=["post"])
    def rotate(self, request, pk=None):
        old_key = self.get_object()
        new_key, full_key = _service.rotate(sdk_key=old_key, user=request.user)
        data = SDKKeySerializer(new_key).data
        data["key"] = full_key
        return Response(data, status=status.HTTP_201_CREATED)
