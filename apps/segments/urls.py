from rest_framework.routers import DefaultRouter

from apps.segments.views import SegmentViewSet

app_name = "segments"

router = DefaultRouter()
router.register("", SegmentViewSet, basename="segments")

urlpatterns = router.urls
