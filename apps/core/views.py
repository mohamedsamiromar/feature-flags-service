from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.services import HealthService

_health = HealthService()


class HealthCheckView(APIView):
    """
    GET /healthz/

    Liveness + readiness probe for load balancers and orchestrators.

    Checks:
      * database  — executes a cheap 'SELECT 1' via Django's DB connection.
      * cache     — writes and reads back a unique sentinel key in Redis.

    Responses:
      200 OK      — all checks passed; body lists each component as "ok".
      503 Service Unavailable — one or more checks failed; body details which.

    Authentication is intentionally disabled so that infrastructure probes
    (ALB, k8s kubelet, Caddy) can reach this endpoint without a JWT token.
    The probe logic lives in HealthService; this view only renders the result.
    """

    permission_classes = [AllowAny]
    # Exclude from any throttle applied globally — health checks must always
    # be able to reach the service even under rate-limit pressure.
    throttle_classes = []

    def get(self, request):
        result = _health.check()
        return Response(result.body, status=result.http_status)
