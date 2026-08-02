"""Query layer for the sdk_keys app — the only place with ORM access for SDK keys."""

from apps.core.errors import APIError, Error
from apps.sdk_keys.models import SDKKey


class SDKKeyQuery:
    @staticmethod
    def get_for_member(pk, user) -> SDKKey:
        """Membership-scoped fetch (access runs through the environment's
        project). 404 if not visible to the caller."""
        try:
            return (
                SDKKey.objects
                .select_related("environment__project")
                .get(pk=pk, environment__project__organization__memberships__user=user)
            )
        except SDKKey.DoesNotExist:
            raise APIError(Error.INSTANCE_NOT_FOUND, extra=["SDK key"])

    @staticmethod
    def list_for_member(user):
        return (
            SDKKey.objects
            .filter(environment__project__organization__memberships__user=user)
            .select_related("environment")
            .order_by("-created_at")
            .distinct()
        )

    @staticmethod
    def create(**fields) -> SDKKey:
        return SDKKey.objects.create(**fields)

    @staticmethod
    def save(sdk_key: SDKKey, update_fields=None) -> SDKKey:
        sdk_key.save(update_fields=update_fields)
        return sdk_key
