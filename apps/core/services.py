from dataclasses import dataclass

from django.db import OperationalError
from rest_framework import status

from apps.core.queries import HealthQuery


@dataclass(frozen=True)
class HealthResult:
    body: dict
    http_status: int


class HealthService:
    """Runs liveness/readiness probes and shapes the response.

    Keeps the try/except and status decision out of the view: the view just
    renders ``result.body`` with ``result.http_status``.
    """

    def check(self) -> HealthResult:
        checks = {}
        failed = False

        try:
            HealthQuery.ping_database()
            checks["database"] = "ok"
        except OperationalError as exc:
            checks["database"] = f"error: {exc}"
            failed = True

        try:
            HealthQuery.ping_cache()
            checks["cache"] = "ok"
        except Exception as exc:  # noqa: BLE001 — any cache failure is a health failure
            checks["cache"] = f"error: {exc}"
            failed = True

        http_status = (
            status.HTTP_503_SERVICE_UNAVAILABLE if failed else status.HTTP_200_OK
        )
        return HealthResult(
            body={"status": "error" if failed else "ok", "checks": checks},
            http_status=http_status,
        )
