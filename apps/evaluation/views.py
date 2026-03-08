from rest_framework import mixins, permissions, status, viewsets
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.core.exceptions import FlagNotFoundError
from apps.evaluation.models import EvaluationLog
from apps.evaluation.serializers import (
    EvaluateRequestSerializer,
    EvaluateResponseSerializer,
    EvaluationLogSerializer,
)
from apps.evaluation.services import FlagEvaluationService
from apps.evaluation.tasks import log_evaluation

_service = FlagEvaluationService()


class EvaluateFlagView(APIView):
    """
    POST /api/v1/evaluation/evaluate/

    Body: { "flag_key": "dark-mode", "user_context": {"country": "EG", ...} }

    Evaluates a flag for the authenticated user's flag set and returns the
    boolean result immediately. Impression logging is dispatched to a Celery
    worker so the HTTP response is never delayed by a DB write.

    Rate-limited via the 'evaluation' throttle scope (default: 1000/minute,
    configurable via the THROTTLE_RATE_EVALUATION environment variable).
    """

    permission_classes = [permissions.IsAuthenticated]
    # ScopedRateThrottle reads throttle_scope and looks up the rate in
    # REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"]["evaluation"] in settings.py.
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "evaluation"

    def post(self, request):
        serializer = EvaluateRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        flag_key = serializer.validated_data["flag_key"]
        user_context = serializer.validated_data["user_context"]

        try:
            # evaluate() returns an EvaluationResult dataclass that contains
            # flag_id, flag_key, and result — a single cache/DB round-trip.
            evaluation = _service.evaluate(
                flag_key=flag_key,
                owner_id=request.user.id,
                user_context=user_context,
            )
        except FlagNotFoundError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_404_NOT_FOUND)

        # Fire-and-forget: the Celery worker writes the EvaluationLog row.
        # The HTTP response is returned to the caller before (and regardless
        # of) the log write completing.
        log_evaluation.delay(
            flag_id=evaluation.flag_id,
            user_id=request.user.id,
            result=evaluation.result,
            context_data=user_context,
        )

        return Response(
            EvaluateResponseSerializer({
                "flag_key": evaluation.flag_key,
                "result": evaluation.result,
            }).data
        )


class EvaluationLogViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Read-only log of past evaluations for the authenticated user's flags."""

    permission_classes = [permissions.IsAuthenticated]
    serializer_class = EvaluationLogSerializer

    def get_queryset(self):
        return (
            EvaluationLog.objects
            .filter(flag__owner=self.request.user)
            .select_related("flag")
        )
