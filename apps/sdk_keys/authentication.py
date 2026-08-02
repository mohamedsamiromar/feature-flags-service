from django.contrib.auth.models import AnonymousUser
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from apps.sdk_keys.key_generator import KeyGenerator
from apps.sdk_keys.models import SDKKey


class SDKKeyAuthentication(BaseAuthentication):
    """
    Authenticates requests carrying an X-SDK-Key header.

    The SDK key itself is the principal — there is no user behind an SDK request
    now that flags belong to projects rather than users. On success returns
    (AnonymousUser, sdk_key) so:
      - request.auth  → the SDKKey instance (project/env + key_type live here)
      - request.user  → AnonymousUser

    Endpoints authorize via ``HasSDKKey`` (checks request.auth), not
    IsAuthenticated. Returns None if the header is absent so DRF falls through to
    the next authentication class (JWT). Raises AuthenticationFailed on a bad key.
    """

    HEADER = "HTTP_X_SDK_KEY"

    def authenticate(self, request):
        raw_key = request.META.get(self.HEADER)
        if not raw_key:
            return None

        hashed = KeyGenerator.hash_raw(raw_key)
        try:
            sdk_key = (
                SDKKey.objects
                .select_related("environment__project")
                .get(hashed_key=hashed, is_active=True)
            )
        except SDKKey.DoesNotExist:
            raise AuthenticationFailed("Invalid or revoked SDK key.")

        # Non-blocking timestamp update — use update() to skip model signals
        SDKKey.objects.filter(pk=sdk_key.pk).update(last_used_at=timezone.now())

        return (AnonymousUser(), sdk_key)

    def authenticate_header(self, request):
        return "X-SDK-Key"
