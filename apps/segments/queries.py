"""Query layer for the segments app — the only place with ORM access.

Also owns the reverse lookup from a segment back to the flags whose rules
reference it, which is what makes segment edits invalidate the right caches.
"""

from apps.core.errors import APIError, Error
from apps.rules.models import Operator, Rule
from apps.segments.models import Segment, SegmentRule, SegmentTarget


class SegmentQuery:
    @staticmethod
    def get_in_project(key: str, project) -> Segment:
        try:
            return Segment.objects.get(key=key, project=project)
        except Segment.DoesNotExist:
            raise APIError(Error.INSTANCE_NOT_FOUND, extra=["Segment"])

    @staticmethod
    def list_for_project(project):
        return Segment.objects.filter(project=project).prefetch_related("targets", "rules")

    @staticmethod
    def create(project, **fields) -> Segment:
        return Segment.objects.create(project=project, **fields)

    @staticmethod
    def save(segment: Segment, update_fields=None) -> Segment:
        segment.save(update_fields=update_fields)
        return segment

    @staticmethod
    def delete(segment: Segment) -> None:
        segment.delete()

    @staticmethod
    def exists_with_key(project, key: str) -> bool:
        return Segment.objects.filter(project=project, key=key).exists()

    # ------------------------------------------------------------------
    # Evaluation support
    # ------------------------------------------------------------------

    @staticmethod
    def evaluation_payload(project_id: int, keys) -> dict:
        """Resolve `keys` into the dict shape ``SegmentEvaluator`` consumes.

        Returns ``{segment_key: {"included": set, "excluded": set, "rules": []}}``.
        Built once per flag when its cache entry is written, so the SDK hot path
        does no segment queries at all.

        Sets are used for the target lists because membership is the only
        question ever asked of them.
        """
        if not keys:
            return {}

        segments = (
            Segment.objects
            .filter(project_id=project_id, key__in=set(keys))
            .prefetch_related("targets", "rules")
        )

        payload = {}
        for segment in segments:
            included, excluded = set(), set()
            for target in segment.targets.all():
                (excluded if target.excluded else included).add(target.user_key)
            payload[segment.key] = {
                "included": included,
                "excluded": excluded,
                "rules": [
                    {
                        "attribute": rule.attribute,
                        "operator": rule.operator,
                        "value": rule.value,
                    }
                    for rule in segment.rules.all()
                ],
            }
        return payload

    @staticmethod
    def referencing_flags(segment: Segment):
        """Every flag with a rule pointing at `segment`.

        A segment edit changes the answer for all of them, so each one's cache
        must be evicted — without this, a segment change would take up to the
        full cache TTL to take effect.
        """
        from apps.flags.models import FeatureFlag

        flag_ids = (
            Rule.objects
            .filter(
                operator__in=Operator.segment_operators(),
                value=segment.key,
                flag__project_id=segment.project_id,
            )
            .values_list("flag_id", flat=True)
        )
        return FeatureFlag.objects.filter(id__in=set(flag_ids))

    @staticmethod
    def is_referenced(segment: Segment) -> bool:
        return Rule.objects.filter(
            operator__in=Operator.segment_operators(),
            value=segment.key,
            flag__project_id=segment.project_id,
        ).exists()


class SegmentTargetQuery:
    @staticmethod
    def list_for_segment(segment: Segment):
        return SegmentTarget.objects.filter(segment=segment)

    @staticmethod
    def find(segment: Segment, user_key: str):
        return SegmentTarget.objects.filter(segment=segment, user_key=user_key).first()

    @staticmethod
    def get_for_segment(segment: Segment, user_key: str) -> SegmentTarget:
        try:
            return SegmentTarget.objects.get(segment=segment, user_key=user_key)
        except SegmentTarget.DoesNotExist:
            raise APIError(Error.INSTANCE_NOT_FOUND, extra=["Segment target"])

    @staticmethod
    def upsert(segment: Segment, user_key: str, excluded: bool):
        """Idempotent: moving a user between the include and exclude list is an
        update, not a duplicate row."""
        target, created = SegmentTarget.objects.update_or_create(
            segment=segment,
            user_key=user_key,
            defaults={"excluded": excluded},
        )
        return target, created

    @staticmethod
    def delete(target: SegmentTarget) -> None:
        target.delete()


class SegmentRuleQuery:
    @staticmethod
    def list_for_segment(segment: Segment):
        return SegmentRule.objects.filter(segment=segment)

    @staticmethod
    def get_for_segment(segment: Segment, rule_id) -> SegmentRule:
        try:
            return SegmentRule.objects.get(pk=rule_id, segment=segment)
        except SegmentRule.DoesNotExist:
            raise APIError(Error.INSTANCE_NOT_FOUND, extra=["Segment rule"])

    @staticmethod
    def create(segment: Segment, **fields) -> SegmentRule:
        return SegmentRule.objects.create(segment=segment, **fields)

    @staticmethod
    def save(rule: SegmentRule, update_fields=None) -> SegmentRule:
        rule.save(update_fields=update_fields)
        return rule

    @staticmethod
    def delete(rule: SegmentRule) -> None:
        rule.delete()
