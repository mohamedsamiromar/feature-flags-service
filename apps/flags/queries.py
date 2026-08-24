"""Query layer for the flags app.

The ONLY place that touches the ORM for flags, variations, and versions. Every
read, write, and lock lives here; services call these and never see a manager.
Lookups raise ``APIError(Error.INSTANCE_NOT_FOUND, ...)`` so the service layer
stays free of ORM exception handling.
"""

from django.db import transaction
from django.db.models import Max

from apps.core.errors import APIError, Error
from apps.flags.models import (
    FeatureFlag,
    FlagPrerequisite,
    FlagTarget,
    FlagVersion,
    Variation,
)


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

    @staticmethod
    def env_ids_by_flag(flags) -> dict:
        """``{flag_id: [env_id, ...]}`` for many flags in ONE query.

        A segment edit invalidates every flag referencing it; doing that with
        `env_ids_for` per flag is a query per flag.
        """
        from apps.environment.models import EnvironmentFlag

        mapping = {}
        rows = (
            EnvironmentFlag.objects
            .filter(feature_flag__in=flags)
            .values_list("feature_flag_id", "environment_id")
        )
        for flag_id, env_id in rows:
            mapping.setdefault(flag_id, []).append(env_id)
        return mapping


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


class FlagTargetQuery:
    """ORM access for individual user targets."""

    @staticmethod
    def list_for_flag(flag: FeatureFlag):
        return FlagTarget.objects.filter(flag=flag).select_related("variation")

    @staticmethod
    def get_for_flag(flag: FeatureFlag, user_key: str) -> FlagTarget:
        try:
            return FlagTarget.objects.select_related("variation").get(
                flag=flag, user_key=user_key
            )
        except FlagTarget.DoesNotExist:
            raise APIError(Error.INSTANCE_NOT_FOUND, extra=["Target"])

    @staticmethod
    def find(flag: FeatureFlag, user_key: str):
        """Return the user's target, or None. Unlike `get_for_flag`, absence is
        not an error — used when upserting, where "no target yet" is normal."""
        return FlagTarget.objects.select_related("variation").filter(
            flag=flag, user_key=user_key
        ).first()

    @staticmethod
    def upsert(flag: FeatureFlag, user_key: str, variation: Variation):
        """Pin `user_key` to `variation`, replacing any existing target.

        Returns ``(target, created)``. Upsert rather than create so re-targeting
        a user is a single idempotent call instead of delete-then-create.
        """
        target, created = FlagTarget.objects.update_or_create(
            flag=flag,
            user_key=user_key,
            defaults={"variation": variation},
        )
        return target, created

    @staticmethod
    def delete(target: FlagTarget) -> None:
        target.delete()


class FlagPrerequisiteQuery:
    """ORM access for prerequisite gates, including the graph walks that keep
    the dependency graph acyclic."""

    @staticmethod
    def list_for_flag(flag: FeatureFlag):
        return (
            FlagPrerequisite.objects
            .filter(flag=flag)
            .select_related("prerequisite_flag", "required_variation")
        )

    @staticmethod
    def get_for_flag(flag: FeatureFlag, prerequisite_key: str) -> FlagPrerequisite:
        try:
            return FlagPrerequisite.objects.select_related(
                "prerequisite_flag", "required_variation"
            ).get(flag=flag, prerequisite_flag__key=prerequisite_key)
        except FlagPrerequisite.DoesNotExist:
            raise APIError(Error.INSTANCE_NOT_FOUND, extra=["Prerequisite"])

    @staticmethod
    def upsert(flag: FeatureFlag, prerequisite_flag: FeatureFlag, required_variation: Variation):
        obj, created = FlagPrerequisite.objects.update_or_create(
            flag=flag,
            prerequisite_flag=prerequisite_flag,
            defaults={"required_variation": required_variation},
        )
        return obj, created

    @staticmethod
    def delete(prerequisite: FlagPrerequisite) -> None:
        prerequisite.delete()

    @staticmethod
    def dependents_of(flag: FeatureFlag):
        """Flags that name `flag` as a prerequisite."""
        return FeatureFlag.objects.filter(prerequisites__prerequisite_flag=flag).distinct()

    @staticmethod
    def reaches(start: FeatureFlag, target_id: int, max_depth: int = 25) -> list:
        """Walk the prerequisite graph from `start`; return the path to
        `target_id` if one exists, else [].

        Used to reject a new edge that would close a cycle. Breadth-first with
        a visited set, so an already-corrupted graph cannot hang the walk, and
        a depth cap as a final backstop.
        """
        seen = {start.id}
        queue = [(start, [start.key])]
        depth = 0
        while queue and depth < max_depth:
            next_queue = []
            for node, path in queue:
                if node.id == target_id:
                    return path
                edges = (
                    FlagPrerequisite.objects
                    .filter(flag=node)
                    .select_related("prerequisite_flag")
                )
                for edge in edges:
                    nxt = edge.prerequisite_flag
                    if nxt.id in seen:
                        continue
                    seen.add(nxt.id)
                    next_queue.append((nxt, path + [nxt.key]))
            queue = next_queue
            depth += 1
        return []
