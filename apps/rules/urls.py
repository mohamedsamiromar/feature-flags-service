from rest_framework.routers import DefaultRouter

from apps.rules.views import RuleViewSet

app_name = "rules"

router = DefaultRouter()
router.register("", RuleViewSet, basename="rules")

urlpatterns = router.urls
