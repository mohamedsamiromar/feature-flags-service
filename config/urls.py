from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from apps.core.views import HealthCheckView

api_patterns = [
    path("auth/",       include("apps.accounts.urls")),
    path("flags/",      include("apps.flags.urls")),
    path("rules/",      include("apps.rules.urls")),
    path("targeting/",  include("apps.targeting.urls")),
    path("evaluation/", include("apps.evaluation.urls")),
    path("audit/",      include("apps.audit.urls")),
]

urlpatterns = [
    # Infrastructure probe — no auth required, not versioned.
    # Used by ALBs, k8s liveness/readiness probes, and Docker HEALTHCHECK.
    path("healthz/", HealthCheckView.as_view(), name="healthz"),

    path("admin/", admin.site.urls),
    path(f"api/{settings.API_VERSION}/", include(api_patterns)),
]
