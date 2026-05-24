from django.core.exceptions import PermissionDenied

from apps.environment.models import Environment
from apps.sdk_keys.key_generator import KeyGenerator
from apps.sdk_keys.models import SDKKey


class SDKKeyService:
    def create_key(self, user, environment_id: int, name: str, key_type: str) -> tuple[SDKKey, str]:
        """
        Create a new SDK key for the given environment.
        Returns (sdk_key, full_key). full_key is shown once — callers must
        surface it immediately; it cannot be recovered from the database.
        """
        env = Environment.objects.get(pk=environment_id, owner=user)
        full_key, prefix, hashed = KeyGenerator.generate(key_type)
        sdk_key = SDKKey.objects.create(
            name=name,
            prefix=prefix,
            hashed_key=hashed,
            environment=env,
            key_type=key_type,
        )
        return sdk_key, full_key

    def revoke(self, sdk_key: SDKKey, user) -> SDKKey:
        self._assert_owner(sdk_key, user)
        sdk_key.is_active = False
        sdk_key.save(update_fields=["is_active", "updated_at"])
        return sdk_key

    def rotate(self, sdk_key: SDKKey, user) -> tuple[SDKKey, str]:
        """
        Deactivate the old key and issue a replacement with the same metadata.
        Returns (new_sdk_key, new_full_key).
        """
        self._assert_owner(sdk_key, user)
        self.revoke(sdk_key, user)
        return self.create_key(
            user=user,
            environment_id=sdk_key.environment_id,
            name=sdk_key.name,
            key_type=sdk_key.key_type,
        )

    @staticmethod
    def _assert_owner(sdk_key: SDKKey, user) -> None:
        if sdk_key.environment.owner_id != user.id:
            raise PermissionDenied("You do not own this SDK key.")
