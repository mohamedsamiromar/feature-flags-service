from rest_framework.routers import DefaultRouter

from apps.evaluation.views import EvaluationLogViewSet

app_name = "evaluation"

router = DefaultRouter()
router.register("logs", EvaluationLogViewSet, basename="evaluation-logs")

urlpatterns = router.urls
