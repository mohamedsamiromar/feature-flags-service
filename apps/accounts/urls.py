from django.urls import path
from rest_framework_simplejwt.views import (
    TokenBlacklistView,
    TokenObtainPairView,
    TokenRefreshView,
)

from apps.accounts.views import RegisterView

app_name = "accounts"

urlpatterns = [
    # POST {"username": "...", "email": "...", "password": "..."}
    # → 201 {user, organization, project, environments, access, refresh}
    path("register/", RegisterView.as_view(), name="register"),
    # POST {"username": "...", "password": "..."} → {"access": "...", "refresh": "..."}
    path("token/", TokenObtainPairView.as_view(), name="token-obtain"),
    # POST {"refresh": "..."} → {"access": "..."}
    path("token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    # POST {"refresh": "..."} → 200 — logout: the refresh token stops working.
    # The access token it minted lives out its (short) lifetime.
    path("token/blacklist/", TokenBlacklistView.as_view(), name="token-blacklist"),
]
