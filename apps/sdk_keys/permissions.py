from rest_framework.permissions import BasePermission

from apps.sdk_keys.models import SDKKey


class HasSDKKey(BasePermission):
    """Grants access when the request was authenticated by a valid SDK key.

    Used in place of IsAuthenticated on SDK endpoints: the SDK key is the
    principal, so there is no authenticated User to check."""

    message = "A valid X-SDK-Key header is required."

    def has_permission(self, request, view):
        return isinstance(request.auth, SDKKey)
