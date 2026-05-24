from django.urls import path

from apps.sdk.views import SDKEvaluateFlagView

app_name = "sdk"

urlpatterns = [
    path("evaluate/", SDKEvaluateFlagView.as_view(), name="evaluate"),
]
