from django.core.cache import cache
from django.core.exceptions import PermissionDenied

from apps.audit.services import AuditService
from apps.core.exceptions import FlagNotFoundError
from apps.flags.models import FeatureFlag


class FlagService:
    def get_by_key(self, key: str, user) -> FeatureFlag:
        try:
            return FeatureFlag.objects.get(key=key, owner=user)
        except FeatureFlag.DoesNotExist:
            raise FlagNotFoundError(f"Flag '{key}' not found")

    def create_flag(self, user, **kwargs) -> FeatureFlag:
        flag = FeatureFlag.objects.create(owner=user, **kwargs)
        AuditService.log(
            user=user,
            action=AuditService.CREATE,
            entity=flag,
            old_value=None,
            new_value=AuditService.snapshot(flag),
        )
        return flag

    def update_flag(self, flag: FeatureFlag, user, **kwargs) -> FeatureFlag:
        self._assert_owner(flag, user)
        old_snapshot = AuditService.snapshot(flag)

        for attr, value in kwargs.items():
            setattr(flag, attr, value)
        flag.save()
        self._invalidate_cache(user.id, flag.key)

        AuditService.log(
            user=user,
            action=AuditService.UPDATE,
            entity=flag,
            old_value=old_snapshot,
            new_value=AuditService.snapshot(flag),
        )
        return flag

    def delete_flag(self, flag: FeatureFlag, user) -> None:
        self._assert_owner(flag, user)
        old_snapshot = AuditService.snapshot(flag)
        # Capture key before deletion so cache invalidation still works
        flag_key = flag.key
        flag.delete()
        self._invalidate_cache(user.id, flag_key)

        # pk becomes None after delete — restore it so AuditLog.entity_id is
        # populated with the ID of the flag that was deleted.
        flag.pk = old_snapshot["id"]
        AuditService.log(
            user=user,
            action=AuditService.DELETE,
            entity=flag,
            old_value=old_snapshot,
            new_value=None,
        )

    # ------------------------------------------------------------------

    @staticmethod
    def _assert_owner(flag: FeatureFlag, user) -> None:
        if flag.owner_id != user.id:
            raise PermissionDenied("You do not own this flag.")

    @staticmethod
    def _invalidate_cache(owner_id: int, flag_key: str) -> None:
        cache.delete(f"flags:{owner_id}:{flag_key}")
