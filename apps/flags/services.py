from django.core.cache import cache
from django.core.exceptions import PermissionDenied

from apps.audit.services import AuditService
from apps.core.exceptions import FlagArchivedError, FlagNotFoundError
from apps.flags.models import FeatureFlag, Variation


class FlagService:
    def get_by_key(self, key: str, user) -> FeatureFlag:
        try:
            return FeatureFlag.objects.get(key=key, owner=user)
        except FeatureFlag.DoesNotExist:
            raise FlagNotFoundError(f"Flag '{key}' not found")

    def create_flag(self, user, **kwargs) -> FeatureFlag:
        flag_type = kwargs.get("flag_type", FeatureFlag.FlagType.BOOLEAN)
        flag = FeatureFlag.objects.create(owner=user, **kwargs)

        if flag_type == FeatureFlag.FlagType.BOOLEAN:
            self._create_boolean_variations(flag)

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
        if flag.is_archived:
            raise FlagArchivedError()
        old_snapshot = AuditService.snapshot(flag)

        for attr, value in kwargs.items():
            setattr(flag, attr, value)
        flag.save()
        self._invalidate_all_env_caches(flag)

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
        flag_key = flag.key
        flag.delete()
        self._invalidate_cache(user.id, flag_key)

        flag.pk = old_snapshot["id"]
        AuditService.log(
            user=user,
            action=AuditService.DELETE,
            entity=flag,
            old_value=old_snapshot,
            new_value=None,
        )

    def archive_flag(self, flag: FeatureFlag, user) -> FeatureFlag:
        self._assert_owner(flag, user)
        old_snapshot = AuditService.snapshot(flag)
        flag.is_archived = True
        flag.save(update_fields=["is_archived", "updated_at"])
        self._invalidate_all_env_caches(flag)
        AuditService.log(
            user=user,
            action=AuditService.ARCHIVE,
            entity=flag,
            old_value=old_snapshot,
            new_value=AuditService.snapshot(flag),
        )
        return flag

    def unarchive_flag(self, flag: FeatureFlag, user) -> FeatureFlag:
        self._assert_owner(flag, user)
        old_snapshot = AuditService.snapshot(flag)
        flag.is_archived = False
        flag.save(update_fields=["is_archived", "updated_at"])
        self._invalidate_all_env_caches(flag)
        AuditService.log(
            user=user,
            action=AuditService.UNARCHIVE,
            entity=flag,
            old_value=old_snapshot,
            new_value=AuditService.snapshot(flag),
        )
        return flag

    # ------------------------------------------------------------------
    # Variation management
    # ------------------------------------------------------------------

    def create_variation(
        self, flag: FeatureFlag, user, name: str, value_type: str, value
    ) -> Variation:
        self._assert_owner(flag, user)
        variation = Variation.objects.create(
            flag=flag, name=name, value_type=value_type, value=value
        )
        self._invalidate_all_env_caches(flag)
        return variation

    def update_variation(
        self, variation: Variation, user, **kwargs
    ) -> Variation:
        self._assert_owner(variation.flag, user)
        for attr, value in kwargs.items():
            setattr(variation, attr, value)
        variation.save()
        self._invalidate_all_env_caches(variation.flag)
        return variation

    def delete_variation(self, variation: Variation, user) -> None:
        self._assert_owner(variation.flag, user)
        flag = variation.flag
        variation.delete()
        self._invalidate_all_env_caches(flag)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _create_boolean_variations(self, flag: FeatureFlag) -> None:
        true_var = Variation.objects.create(
            flag=flag, name="true", value_type=Variation.ValueType.BOOLEAN, value=True
        )
        false_var = Variation.objects.create(
            flag=flag, name="false", value_type=Variation.ValueType.BOOLEAN, value=False
        )
        flag.fallthrough_variation = true_var
        flag.off_variation = false_var
        flag.save(update_fields=["fallthrough_variation", "off_variation"])

    @staticmethod
    def _assert_owner(flag: FeatureFlag, user) -> None:
        if flag.owner_id != user.id:
            raise PermissionDenied("You do not own this flag.")

    @staticmethod
    def _invalidate_cache(owner_id: int, flag_key: str) -> None:
        cache.delete(f"flags:{owner_id}:{flag_key}")

    @staticmethod
    def _invalidate_all_env_caches(flag: FeatureFlag) -> None:
        from apps.environment.models import EnvironmentFlag
        from apps.evaluation.services import FlagEvaluationService
        env_flags = EnvironmentFlag.objects.filter(
            feature_flag=flag
        ).values_list("environment_id", flat=True)
        for env_id in env_flags:
            FlagEvaluationService.invalidate_cache(
                owner_id=flag.owner_id,
                flag_key=flag.key,
                env_id=env_id,
            )
