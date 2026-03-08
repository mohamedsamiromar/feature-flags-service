from django.urls import path
from rest_framework.routers import DefaultRouter

from apps.evaluation.views import EvaluateFlagView, EvaluationLogViewSet

app_name = "evaluation"

router = DefaultRouter()
router.register("logs", EvaluationLogViewSet, basename="evaluation-logs")

urlpatterns = [
    path("evaluate/", EvaluateFlagView.as_view(), name="evaluate"),
    *router.urls,
]
