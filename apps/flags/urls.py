from rest_framework.routers import DefaultRouter

from apps.flags.views import FeatureFlagViewSet

app_name = "flags"

router = DefaultRouter()
router.register("", FeatureFlagViewSet, basename="flags")

urlpatterns = router.urls
