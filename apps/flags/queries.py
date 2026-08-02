"""Query layer for the flags app.

The ONLY place that touches the ORM for flags, variations, and versions. Every
read, write, and lock lives here; services call these and never see a manager.
Lookups raise ``APIError(Error.INSTANCE_NOT_FOUND, ...)`` so the service layer
stays free of ORM exception handling.
"""

from django.db import transaction
from django.db.models import Max

from apps.core.errors import APIError, Error
from apps.flags.models import FeatureFlag, FlagVersion, Variation


class FlagQuery:
    @staticmethod
    def get_in_project(key: str, project) -> FeatureFlag:
        """Project-scoped fetch (any archived state). Missing → 404."""
        try:
            return FeatureFlag.objects.get(key=key, project=project)
        except FeatureFlag.DoesNotExist:
            raise APIError(Error.INSTANCE_NOT_FOUND, extra=["Flag"])

    @staticmethod
    def list_for_project(project, include_archived: bool = False):
        qs = FeatureFlag.objects.filter(project=project).prefetch_related("rules")
        if include_archived:
            return qs
        return qs.filter(is_archived=False)

    @staticmethod
    def create(project, **fields) -> FeatureFlag:
        return FeatureFlag.objects.create(project=project, **fields)

    @staticmethod
    def save(flag: FeatureFlag, update_fields=None) -> FeatureFlag:
        flag.save(update_fields=update_fields)
        return flag

    @staticmethod
    def delete(flag: FeatureFlag) -> None:
        flag.delete()

    @staticmethod
    def env_ids_for(flag: FeatureFlag) -> list:
        from apps.environment.models import EnvironmentFlag
        return list(
            EnvironmentFlag.objects
            .filter(feature_flag=flag)
            .values_list("environment_id", flat=True)
        )


class VariationQuery:
    @staticmethod
    def get_for_flag(flag: FeatureFlag, variation_id) -> Variation:
        try:
            return Variation.objects.get(pk=variation_id, flag=flag)
        except Variation.DoesNotExist:
            raise APIError(Error.INSTANCE_NOT_FOUND, extra=["Variation"])

    @staticmethod
    def list_for_flag(flag: FeatureFlag):
        return Variation.objects.filter(flag=flag)

    @staticmethod
    def ids_for_flag(flag: FeatureFlag) -> set:
        return set(Variation.objects.filter(flag=flag).values_list("id", flat=True))

    @staticmethod
    def create(flag: FeatureFlag, **fields) -> Variation:
        return Variation.objects.create(flag=flag, **fields)

    @staticmethod
    def save(variation: Variation, update_fields=None) -> Variation:
        variation.save(update_fields=update_fields)
        return variation

    @staticmethod
    def delete(variation: Variation) -> None:
        variation.delete()


class FlagVersionQuery:
    @staticmethod
    def list_for_flag(flag: FeatureFlag):
        return FlagVersion.objects.filter(flag=flag)  # newest-first via model ordering

    @staticmethod
    def get(flag: FeatureFlag, version_no) -> FlagVersion:
        try:
            return FlagVersion.objects.get(flag=flag, version_no=version_no)
        except FlagVersion.DoesNotExist:
            raise APIError(Error.INSTANCE_NOT_FOUND, extra=["Flag version"])

    @staticmethod
    def create_next(
        flag: FeatureFlag,
        snapshot: dict,
        change_action: str,
        changed_by,
        source_version_no: int = None,
    ) -> FlagVersion:
        """Append the next version, allocating version_no under a row lock so
        concurrent mutations cannot claim the same number."""
        with transaction.atomic():
            last = (
                FlagVersion.objects
                .select_for_update()
                .filter(flag=flag)
                .aggregate(m=Max("version_no"))["m"]
            )
            return FlagVersion.objects.create(
                flag=flag,
                version_no=(last or 0) + 1,
                snapshot=snapshot,
                change_action=change_action,
                source_version_no=source_version_no,
                changed_by=changed_by,
            )
