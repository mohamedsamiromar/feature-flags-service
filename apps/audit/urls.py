from rest_framework.routers import DefaultRouter

from apps.audit.views import AuditLogViewSet

app_name = "audit"

router = DefaultRouter()
router.register("", AuditLogViewSet, basename="audit")

urlpatterns = router.urls
