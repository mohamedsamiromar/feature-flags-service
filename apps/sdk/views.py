from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.core.exceptions import FlagNotFoundError
from apps.evaluation.services import FlagEvaluationService
from apps.evaluation.tasks import log_evaluation
from apps.sdk.serializers import SDKEvaluateRequestSerializer, SDKEvaluateResponseSerializer
from apps.sdk_keys.authentication import SDKKeyAuthentication

_eval_service = FlagEvaluationService()


class SDKEvaluateFlagView(APIView):
    """
    POST /api/v1/sdk/evaluate/
    Header: X-SDK-Key: sdk_srv_<token>

    Body: { "flag_key": "dark-mode", "user_context": {"user_id": "u123"} }

    SDK-key-only evaluation endpoint. env_id is derived from the key itself,
    so callers never need to pass it. Both server and client keys are accepted.
    """

    authentication_classes = [SDKKeyAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "evaluation"

    def post(self, request):
        # SDKKeyAuthentication guarantees request.auth is an SDKKey instance
        # for every authenticated request on this endpoint.
        sdk_key = request.auth

        serializer = SDKEvaluateRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        flag_key = serializer.validated_data["flag_key"]
        user_context = serializer.validated_data["user_context"]

        try:
            evaluation = _eval_service.evaluate(
                flag_key=flag_key,
                owner_id=sdk_key.environment.owner_id,
                user_context=user_context,
                env_id=sdk_key.environment_id,
            )
        except FlagNotFoundError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)

        log_evaluation.delay(
            flag_id=evaluation.flag_id,
            user_id=request.user.id,
            result=evaluation.result,
            context_data=user_context,
        )

        return Response(
            SDKEvaluateResponseSerializer({
                "flag_key": evaluation.flag_key,
                "result": evaluation.result,
                "environment": sdk_key.environment.name,
            }).data
        )
