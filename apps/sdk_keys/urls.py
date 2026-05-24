from rest_framework.routers import DefaultRouter

from apps.sdk_keys.views import SDKKeyViewSet

app_name = "sdk_keys"

router = DefaultRouter()
router.register("", SDKKeyViewSet, basename="sdk-keys")

urlpatterns = router.urls
