from django.urls import path

from apps.sdk.views import SDKEvaluateAllFlagsView, SDKEvaluateFlagView

app_name = "sdk"

urlpatterns = [
    path("evaluate/", SDKEvaluateFlagView.as_view(), name="evaluate"),
    path("flags/evaluate/", SDKEvaluateAllFlagsView.as_view(), name="evaluate-all"),
]
