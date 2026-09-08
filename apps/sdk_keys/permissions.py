from rest_framework.permissions import BasePermission

from apps.sdk_keys.models import SDKKey


class HasSDKKey(BasePermission):
    """Grants access when the request was authenticated by a valid SDK key.

    Used in place of IsAuthenticated on SDK endpoints: the SDK key is the
    principal, so there is no authenticated User to check."""

    message = "A valid X-SDK-Key header is required."

    def has_permission(self, request, view):
        return isinstance(request.auth, SDKKey)


class HasServerSDKKey(BasePermission):
    """Grants access only to a **server** SDK key. A client key gets 403.

    Every other SDK endpoint accepts both key types. The config download cannot,
    because its payload contains `targets` (individual user keys) and segment
    `included`/`excluded` lists — in practice real identifiers: emails, account
    ids, internal user ids. `sdk_cli_` keys ship to browsers and are readable by
    anyone who opens devtools, so serving this payload to one would publish the
    customer's user list.

    403 rather than 401: the key is valid and the caller is authenticated. It is
    the wrong *kind* of credential, and saying so is what tells an SDK author to
    switch keys instead of hunting a bad token.
    """

    message = "This endpoint requires a server SDK key (sdk_srv_)."

    def has_permission(self, request, view):
        return (
            isinstance(request.auth, SDKKey)
            and request.auth.key_type == SDKKey.KeyType.SERVER
        )
