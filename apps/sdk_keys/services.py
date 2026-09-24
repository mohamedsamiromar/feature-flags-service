from apps.audit.services import AuditService
from apps.core.errors import APIError, Error
from apps.environment.queries import EnvironmentQuery
from apps.organizations.services import AccessService
from apps.sdk_keys.key_generator import KeyGenerator
from apps.sdk_keys.models import SDKKey
from apps.sdk_keys.queries import SDKKeyQuery


class SDKKeyService:
    """Business logic for SDK keys. Access is enforced through project
    membership (a key or environment in a project you are not a member of is
    invisible); issuing, revoking, and rotating keys require a MEMBER+ role.

    Every mutation is audited. These are long-lived production credentials, so
    "who issued this key, and when was it revoked" is the first question asked
    after an incident. Snapshots are redacted — ``AuditService.REDACTED_FIELDS``
    strips ``hashed_key``, because hash-only storage is only meaningful while
    the digest lives in exactly one table."""

    def create_key(self, user, environment_id: int, name: str, key_type: str):
        """Create a new SDK key for the given environment.

        Returns (sdk_key, full_key). full_key is shown once — callers must
        surface it immediately; it cannot be recovered from the database.
        """
        environment = EnvironmentQuery.get_for_member(environment_id, user)
        AccessService.assert_can_write(user, environment.project)

        full_key, prefix, hashed = KeyGenerator.generate(key_type)
        sdk_key = SDKKeyQuery.create(
            name=name,
            prefix=prefix,
            hashed_key=hashed,
            environment_id=environment_id,
            key_type=key_type,
        )
        AuditService.log(
            user=user,
            action=AuditService.CREATE,
            entity=sdk_key,
            old_value=None,
            new_value=AuditService.snapshot(sdk_key),
        )
        return sdk_key, full_key

    def revoke(self, pk, user) -> SDKKey:
        sdk_key = SDKKeyQuery.get_for_member(pk, user)
        AccessService.assert_can_write(user, sdk_key.environment.project)
        if not sdk_key.is_active:
            raise APIError(Error.ALREADY_IN_STATE, extra=["Key", "revoked"])

        old_snapshot = AuditService.snapshot(sdk_key)
        self._deactivate(sdk_key)
        AuditService.log(
            user=user,
            action=AuditService.REVOKE,
            entity=sdk_key,
            old_value=old_snapshot,
            new_value=AuditService.snapshot(sdk_key),
        )
        return sdk_key

    def rotate(self, pk, user):
        """Deactivate the old key and issue a replacement with the same metadata.
        Returns (new_sdk_key, new_full_key)."""
        old = SDKKeyQuery.get_for_member(pk, user)
        AccessService.assert_can_write(user, old.environment.project)

        old_snapshot = AuditService.snapshot(old)
        self._deactivate(old)
        # Logged as ROTATE, not REVOKE: the two are different events to whoever
        # reads this trail later. A revoked key was taken out of service; a
        # rotated one was replaced, and `create_key` writes the CREATE entry for
        # its replacement immediately after.
        AuditService.log(
            user=user,
            action=AuditService.ROTATE,
            entity=old,
            old_value=old_snapshot,
            new_value=AuditService.snapshot(old),
        )
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
