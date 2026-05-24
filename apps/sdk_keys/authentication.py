from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from apps.sdk_keys.key_generator import KeyGenerator
from apps.sdk_keys.models import SDKKey


class SDKKeyAuthentication(BaseAuthentication):
    """
    Authenticates requests carrying an X-SDK-Key header.

    On success returns (environment.owner, sdk_key) so:
      - request.user  → the flag-owning User (for existing service logic)
      - request.auth  → the SDKKey instance (for env_id and key_type checks)

    Returns None if the header is absent so DRF falls through to the next
    authentication class (JWT). Raises AuthenticationFailed on a bad key.
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
                .select_related("environment__owner")
                .get(hashed_key=hashed, is_active=True)
            )
        except SDKKey.DoesNotExist:
            raise AuthenticationFailed("Invalid or revoked SDK key.")

        # Non-blocking timestamp update — use update() to skip model signals
        SDKKey.objects.filter(pk=sdk_key.pk).update(last_used_at=timezone.now())

        return (sdk_key.environment.owner, sdk_key)

    def authenticate_header(self, request):
        return "X-SDK-Key"
