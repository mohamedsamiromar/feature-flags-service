from rest_framework.routers import DefaultRouter

from apps.environment.views import EnvironmentViewSet

app_name = "environment"

router = DefaultRouter()
router.register("", EnvironmentViewSet, basename="environments")

urlpatterns = router.urls
