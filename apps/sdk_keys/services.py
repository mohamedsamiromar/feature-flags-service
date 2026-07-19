from apps.core.errors import APIError, Error
from apps.environment.queries import EnvironmentQuery
from apps.sdk_keys.key_generator import KeyGenerator
from apps.sdk_keys.models import SDKKey
from apps.sdk_keys.queries import SDKKeyQuery


class SDKKeyService:
    """Business logic for SDK keys. Ownership is enforced through owner-scoped
    queries (a key or environment you don't own is invisible)."""

    def create_key(self, user, environment_id: int, name: str, key_type: str):
        """Create a new SDK key for the given environment.

        Returns (sdk_key, full_key). full_key is shown once — callers must
        surface it immediately; it cannot be recovered from the database.
        """
        if not EnvironmentQuery.exists_for_owner(environment_id, user):
            raise APIError(Error.INVALID_ENVIRONMENT)

        full_key, prefix, hashed = KeyGenerator.generate(key_type)
        sdk_key = SDKKeyQuery.create(
            name=name,
            prefix=prefix,
            hashed_key=hashed,
            environment_id=environment_id,
            key_type=key_type,
        )
        return sdk_key, full_key

    def revoke(self, pk, user) -> SDKKey:
        sdk_key = SDKKeyQuery.get_owned(pk, user)
        if not sdk_key.is_active:
            raise APIError(Error.ALREADY_IN_STATE, extra=["Key", "revoked"])
        return self._deactivate(sdk_key)

    def rotate(self, pk, user):
        """Deactivate the old key and issue a replacement with the same metadata.
        Returns (new_sdk_key, new_full_key)."""
        old = SDKKeyQuery.get_owned(pk, user)
        self._deactivate(old)
        return self.create_key(
            user=user,
            environment_id=old.environment_id,
            name=old.name,
            key_type=old.key_type,
        )

    @staticmethod
    def _deactivate(sdk_key: SDKKey) -> SDKKey:
        sdk_key.is_active = False
        return SDKKeyQuery.save(sdk_key, update_fields=["is_active", "updated_at"])
