from apps.audit.services import AuditService
from apps.core.errors import APIError, Error
from apps.flags.models import FeatureFlag, FlagVersion
from apps.flags.queries import (
    FlagQuery,
    FlagTargetQuery,
    FlagVersionQuery,
    VariationQuery,
)
from apps.organizations.queries import ProjectQuery
from apps.organizations.services import AccessService


class FlagService:
    """Business logic for flags. Fetches through the query layer, applies rules,
    and persists through the query layer — no ORM access lives here.

    Tenancy is enforced by project membership: a project the caller is not a
    member of (or a flag in another project) surfaces as a 404. Mutations
    additionally require a MEMBER+ role, enforced via ``AccessService``.
    """

    # ------------------------------------------------------------------
    # Access helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _project(project_key: str, user, write: bool = False):
        project = ProjectQuery.get_for_member(project_key, user)
        if write:
            AccessService.assert_can_write(user, project)
        return project

    def _flag(self, project_key: str, user, key: str, write: bool = False) -> FeatureFlag:
        project = self._project(project_key, user, write=write)
        return FlagQuery.get_in_project(key, project)

    # ------------------------------------------------------------------
    # Flag CRUD
    # ------------------------------------------------------------------

    def get_by_key(self, project_key: str, user, key: str) -> FeatureFlag:
        return self._flag(project_key, user, key)

    def create_flag(self, project_key: str, user, **kwargs) -> FeatureFlag:
        project = self._project(project_key, user, write=True)
        flag_type = kwargs.get("flag_type", FeatureFlag.FlagType.BOOLEAN)
        flag = FlagQuery.create(project=project, **kwargs)

        if flag_type == FeatureFlag.FlagType.BOOLEAN:
            self._create_boolean_variations(flag)

        self._record_version(flag, user, FlagVersion.ChangeAction.CREATE)
        AuditService.log(
            user=user,
            action=AuditService.CREATE,
            entity=flag,
            old_value=None,
            new_value=AuditService.snapshot(flag),
        )
        return flag

    # The lookup arg is `flag_key`, not `key`: the view splats serializer data
    # that contains a writable "key" field, and a collision there is a
    # TypeError (a 500), not a validation error.
    def update_flag(self, project_key: str, flag_key: str, user, **kwargs) -> FeatureFlag:
        flag = self._flag(project_key, user, flag_key, write=True)
        self._assert_active(flag)
        # A flag's key is its identity: SDK calls, cache keys, and every
        # FlagVersion snapshot are addressed by it. Renaming would silently
        # break live integrations, so it is fixed at creation.
        new_key = kwargs.pop("key", None)
        if new_key is not None and new_key != flag.key:
            raise APIError(Error.IMMUTABLE_FIELD, extra=["key"])
        self._assert_variations_belong(flag, kwargs)
        old_snapshot = AuditService.snapshot(flag)

        for attr, value in kwargs.items():
            setattr(flag, attr, value)
        FlagQuery.save(flag)
        self.invalidate_flag_caches(flag)

        self._record_version(flag, user, FlagVersion.ChangeAction.UPDATE)
        AuditService.log(
            user=user,
            action=AuditService.UPDATE,
            entity=flag,
            old_value=old_snapshot,
            new_value=AuditService.snapshot(flag),
        )
        return flag

    def delete_flag(self, project_key: str, key: str, user) -> None:
        flag = self._flag(project_key, user, key, write=True)
        old_snapshot = AuditService.snapshot(flag)
        # Capture the cache coordinates before the delete cascades the
        # EnvironmentFlag rows away — afterwards there is no way to learn which
        # environments held a cached copy.
        project_id, flag_key = flag.project_id, flag.key
        env_ids = FlagQuery.env_ids_for(flag)
        FlagQuery.delete(flag)
        self._invalidate_env_caches(project_id, flag_key, env_ids)

        flag.pk = old_snapshot["id"]
        AuditService.log(
            user=user,
            action=AuditService.DELETE,
            entity=flag,
            old_value=old_snapshot,
            new_value=None,
        )

    def archive_flag(self, project_key: str, key: str, user) -> FeatureFlag:
        flag = self._flag(project_key, user, key, write=True)
        if flag.is_archived:
            raise APIError(Error.ALREADY_IN_STATE, extra=["Flag", "archived"])
        old_snapshot = AuditService.snapshot(flag)
        flag.is_archived = True
        FlagQuery.save(flag, update_fields=["is_archived", "updated_at"])
        self.invalidate_flag_caches(flag)
        AuditService.log(
            user=user,
            action=AuditService.ARCHIVE,
            entity=flag,
            old_value=old_snapshot,
            new_value=AuditService.snapshot(flag),
        )
        return flag

    def unarchive_flag(self, project_key: str, key: str, user) -> FeatureFlag:
        flag = self._flag(project_key, user, key, write=True)
        if not flag.is_archived:
            raise APIError(Error.ALREADY_IN_STATE, extra=["Flag", "active"])
        old_snapshot = AuditService.snapshot(flag)
        flag.is_archived = False
        FlagQuery.save(flag, update_fields=["is_archived", "updated_at"])
        self.invalidate_flag_caches(flag)
        AuditService.log(
            user=user,
            action=AuditService.UNARCHIVE,
            entity=flag,
            old_value=old_snapshot,
            new_value=AuditService.snapshot(flag),
        )
        return flag

    def toggle_environment(self, project_key: str, key: str, user, env_name):
        """Flip a flag's per-environment kill switch in one call.

        Creates the EnvironmentFlag on first toggle (off by default, so the
        first call turns the flag on). Delegates the actual EnvironmentFlag
        mutation + audit to EnvironmentFlagService.
        """
        from apps.environment.queries import EnvironmentFlagQuery, EnvironmentQuery
        from apps.environment.services import EnvironmentFlagService

        project = self._project(project_key, user, write=True)
        flag = FlagQuery.get_in_project(key, project)
        self._assert_active(flag)
        if not env_name:
            raise APIError(Error.REQUIRED_FIELD)

        env = EnvironmentQuery.get_in_project_by_name(env_name, project)
        env_flag = EnvironmentFlagQuery.get_or_create(flag, env)
        return EnvironmentFlagService().toggle(env_flag, user)

    # ------------------------------------------------------------------
    # Version history & rollback
    # ------------------------------------------------------------------

    def list_versions(self, project_key: str, key: str, user):
        flag = self._flag(project_key, user, key)
        return FlagVersionQuery.list_for_flag(flag)

    def get_version(self, project_key: str, key: str, user, version_no: int) -> FlagVersion:
        flag = self._flag(project_key, user, key)
        return FlagVersionQuery.get(flag, version_no)

    def rollback(self, project_key: str, key: str, user, version_no: int) -> FeatureFlag:
        """Restore a flag's config to the snapshot in version `version_no`.

        Append-only: the live flag is mutated but a *new* version
        (``change_action=rollback``) is recorded, so history is never rewritten.
        Variation references that no longer exist are dropped to None.
        """
        flag = self._flag(project_key, user, key, write=True)
        self._assert_active(flag)

        version = FlagVersionQuery.get(flag, version_no)

        old_snapshot = AuditService.snapshot(flag)
        self._apply_config(flag, version.snapshot)
        FlagQuery.save(flag)
        self.invalidate_flag_caches(flag)

        self._record_version(
            flag,
            user,
            FlagVersion.ChangeAction.ROLLBACK,
            source_version_no=version_no,
        )
        AuditService.log(
            user=user,
            action=AuditService.ROLLBACK,
            entity=flag,
            old_value=old_snapshot,
            new_value=AuditService.snapshot(flag),
        )
        return flag

    # ------------------------------------------------------------------
    # Variation management
    # ------------------------------------------------------------------

    def list_variations(self, project_key: str, key: str, user):
        flag = self._flag(project_key, user, key)
        return VariationQuery.list_for_flag(flag)

    def create_variation(self, project_key: str, key: str, user, name: str, value_type: str, value):
        flag = self._flag(project_key, user, key, write=True)
        variation = VariationQuery.create(
            flag=flag, name=name, value_type=value_type, value=value
        )
        self.invalidate_flag_caches(flag)
        AuditService.log(
            user=user,
            action=AuditService.CREATE,
            entity=variation,
            old_value=None,
            new_value=AuditService.snapshot(variation),
        )
        return variation

    def update_variation(self, project_key: str, key: str, user, variation_id, **kwargs):
        flag = self._flag(project_key, user, key, write=True)
        variation = VariationQuery.get_for_flag(flag, variation_id)
        old_snapshot = AuditService.snapshot(variation)
        for attr, value in kwargs.items():
            setattr(variation, attr, value)
        VariationQuery.save(variation)
        self.invalidate_flag_caches(flag)
        AuditService.log(
            user=user,
            action=AuditService.UPDATE,
            entity=variation,
            old_value=old_snapshot,
            new_value=AuditService.snapshot(variation),
        )
        return variation

    def delete_variation(self, project_key: str, key: str, user, variation_id) -> None:
        flag = self._flag(project_key, user, key, write=True)
        variation = VariationQuery.get_for_flag(flag, variation_id)
        old_snapshot = AuditService.snapshot(variation)
        VariationQuery.delete(variation)
        self.invalidate_flag_caches(flag)

        # Django clears the pk on delete; restore it so the audit row keeps a
        # usable entity_id (same pattern as delete_flag).
        variation.pk = old_snapshot["id"]
        AuditService.log(
            user=user,
            action=AuditService.DELETE,
            entity=variation,
            old_value=old_snapshot,
            new_value=None,
        )

    # ------------------------------------------------------------------
    # Individual user targeting
    # ------------------------------------------------------------------

    def list_targets(self, project_key: str, key: str, user):
        flag = self._flag(project_key, user, key)
        return FlagTargetQuery.list_for_flag(flag)

    def set_target(self, project_key: str, key: str, user, user_key: str, variation_id):
        """Pin one user to a variation, overriding rules and rollout for them.

        Idempotent: re-targeting an already-targeted user moves them rather
        than erroring, so a dashboard can PUT the desired state blindly.
        """
        flag = self._flag(project_key, user, key, write=True)
        self._assert_active(flag)
        variation = VariationQuery.get_for_flag(flag, variation_id)

        existing = FlagTargetQuery.find(flag, user_key)
        old_snapshot = AuditService.snapshot(existing) if existing else None

        target, created = FlagTargetQuery.upsert(flag, user_key, variation)
        self.invalidate_flag_caches(flag)

        AuditService.log(
            user=user,
            action=AuditService.CREATE if created else AuditService.UPDATE,
            entity=target,
            old_value=old_snapshot,
            new_value=AuditService.snapshot(target),
        )
        return target, created

    def remove_target(self, project_key: str, key: str, user, user_key: str) -> None:
        flag = self._flag(project_key, user, key, write=True)
        self._assert_active(flag)
        target = FlagTargetQuery.get_for_flag(flag, user_key)

        old_snapshot = AuditService.snapshot(target)
        FlagTargetQuery.delete(target)
        self.invalidate_flag_caches(flag)

        # Django clears the pk on delete; restore it so the audit row keeps a
        # usable entity_id (same pattern as delete_flag).
        target.pk = old_snapshot["id"]
        AuditService.log(
            user=user,
            action=AuditService.DELETE,
            entity=target,
            old_value=old_snapshot,
            new_value=None,
        )

    # ------------------------------------------------------------------
    # Internal helpers (pure logic — no ORM)
    # ------------------------------------------------------------------

    # Flag config fields captured in a version snapshot and restored on rollback.
    _CONFIG_FIELDS = (
        "name",
        "description",
        "is_enabled",
        "rollout_percentage",
        "flag_type",
        "off_variation_id",
        "fallthrough_variation_id",
    )

    @staticmethod
    def _assert_active(flag: FeatureFlag) -> None:
        if flag.is_archived:
            raise APIError(Error.FLAG_ARCHIVED)

    @staticmethod
    def _assert_variations_belong(flag: FeatureFlag, kwargs: dict) -> None:
        """A flag's off/fallthrough variation must be one of its own variations."""
        for field in ("off_variation", "fallthrough_variation"):
            variation = kwargs.get(field)
            if variation is not None and variation.flag_id != flag.id:
                raise APIError(Error.VARIATION_NOT_IN_FLAG, extra=["Variation"])

    @classmethod
    def _snapshot_config(cls, flag: FeatureFlag) -> dict:
        """Return the JSON-serialisable, restorable config of `flag`."""
        return {field: getattr(flag, field) for field in cls._CONFIG_FIELDS}

    @classmethod
    def _apply_config(cls, flag: FeatureFlag, snapshot: dict) -> None:
        """Write a snapshot back onto `flag` (does not save).

        Variation FKs are only restored if the referenced variation still
        belongs to this flag; otherwise they are cleared so rollback can never
        point a flag at a deleted or foreign variation.
        """
        valid_variation_ids = VariationQuery.ids_for_flag(flag)
        for field in cls._CONFIG_FIELDS:
            if field not in snapshot:
                continue
            value = snapshot[field]
            if field in ("off_variation_id", "fallthrough_variation_id"):
                value = value if value in valid_variation_ids else None
            setattr(flag, field, value)

    def _record_version(
        self,
        flag: FeatureFlag,
        user,
        change_action: str,
        source_version_no: int = None,
    ) -> FlagVersion:
        return FlagVersionQuery.create_next(
            flag=flag,
            snapshot=self._snapshot_config(flag),
            change_action=change_action,
            changed_by=user,
            source_version_no=source_version_no,
        )

    def _create_boolean_variations(self, flag: FeatureFlag) -> None:
        true_var = VariationQuery.create(
            flag=flag, name="true", value_type="boolean", value=True
        )
        false_var = VariationQuery.create(
            flag=flag, name="false", value_type="boolean", value=False
        )
        flag.fallthrough_variation = true_var
        flag.off_variation = false_var
        FlagQuery.save(flag, update_fields=["fallthrough_variation", "off_variation"])

    @classmethod
    def invalidate_flag_caches(cls, flag: FeatureFlag) -> None:
        """Evict every environment's cached copy of `flag`.

        Public because rule mutations live outside this service but change the
        flag's cached targeting config.
        """
        cls._invalidate_env_caches(flag.project_id, flag.key, FlagQuery.env_ids_for(flag))

    @staticmethod
    def _invalidate_env_caches(project_id: int, flag_key: str, env_ids) -> None:
        from apps.evaluation.services import FlagEvaluationService
        for env_id in env_ids:
            FlagEvaluationService.invalidate_cache(
                project_id=project_id,
                flag_key=flag_key,
                env_id=env_id,
            )
