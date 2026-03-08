import uuid

from django.core.cache import cache
from django.db import connection, OperationalError
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView


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
    """

    permission_classes = [AllowAny]
    # Exclude from any throttle applied globally — health checks must always
    # be able to reach the service even under rate-limit pressure.
    throttle_classes = []

    def get(self, request):
        checks = {}
        failed = False

        # ------------------------------------------------------------------
        # Database probe — a single-row SELECT costs essentially nothing and
        # confirms that the connection pool and PostgreSQL are reachable.
        # ------------------------------------------------------------------
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            checks["database"] = "ok"
        except OperationalError as exc:
            checks["database"] = f"error: {exc}"
            failed = True

        # ------------------------------------------------------------------
        # Redis probe — write a unique sentinel and read it back to confirm
        # both write and read paths work.  A unique value prevents a stale
        # cached result from masking a Redis failure.
        # ------------------------------------------------------------------
        try:
            sentinel_key = f"healthz:{uuid.uuid4().hex}"
            cache.set(sentinel_key, "1", timeout=5)
            if cache.get(sentinel_key) != "1":
                raise RuntimeError("sentinel value mismatch")
            cache.delete(sentinel_key)
            checks["cache"] = "ok"
        except Exception as exc:  # noqa: BLE001
            checks["cache"] = f"error: {exc}"
            failed = True

        # ------------------------------------------------------------------
        # Response
        # ------------------------------------------------------------------
        http_status = status.HTTP_503_SERVICE_UNAVAILABLE if failed else status.HTTP_200_OK
        return Response({"status": "error" if failed else "ok", "checks": checks}, status=http_status)
