from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.serializers import (
    RegisterSerializer,
    RegistrationResponseSerializer,
)
from apps.accounts.services import RegistrationService

_registration_service = RegistrationService()


class RegisterView(APIView):
    """POST /api/v1/auth/register/ — create an account and its personal tenancy.

    Unauthenticated by necessity: this is where a caller gets their first
    credential. It carries its own throttle scope rather than the default
    `anon` bucket, because account creation is the one anonymous endpoint whose
    abuse leaves rows behind — 60/minute of these is 60 orgs, projects, and
    environment sets.
    """

    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "registration"

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = _registration_service.register(**serializer.validated_data)

        refresh = RefreshToken.for_user(result["user"])
        payload = {
            **result,
            "access": str(refresh.access_token),
            "refresh": str(refresh),
        }
        return Response(
            RegistrationResponseSerializer(payload).data,
            status=status.HTTP_201_CREATED,
        )
