"""Query layer for the sdk_keys app — the only place with ORM access for SDK keys."""

from apps.core.errors import APIError, Error
from apps.sdk_keys.models import SDKKey


class SDKKeyQuery:
    @staticmethod
    def get_owned(pk, user) -> SDKKey:
        """Owner-scoped fetch (ownership runs through the environment). 404 if not visible."""
        try:
            return (
                SDKKey.objects
                .select_related("environment")
                .get(pk=pk, environment__owner=user)
            )
        except SDKKey.DoesNotExist:
            raise APIError(Error.INSTANCE_NOT_FOUND, extra=["SDK key"])

    @staticmethod
    def list_for_owner(user):
        return (
            SDKKey.objects
            .filter(environment__owner=user)
            .select_related("environment")
            .order_by("-created_at")
        )

    @staticmethod
    def create(**fields) -> SDKKey:
        return SDKKey.objects.create(**fields)

    @staticmethod
    def save(sdk_key: SDKKey, update_fields=None) -> SDKKey:
        sdk_key.save(update_fields=update_fields)
        return sdk_key
