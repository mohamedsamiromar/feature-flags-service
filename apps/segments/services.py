from apps.audit.services import AuditService
from apps.core.errors import APIError, Error
from apps.flags.services import FlagService
from apps.organizations.queries import ProjectQuery
from apps.organizations.services import AccessService
from apps.rules.models import Operator
from apps.segments.models import Segment
from apps.segments.queries import SegmentQuery, SegmentRuleQuery, SegmentTargetQuery


class SegmentService:
    """Business logic for reusable segments.

    Tenancy follows the same shape as flags: a project the caller is not a
    member of is a 404, and mutations need MEMBER+.

    Every mutation here fans out cache invalidation to *all* flags whose rules
    reference the segment — editing a segment silently changes what those flags
    serve, so their cached config is stale the moment it changes.
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

    def _segment(self, project_key: str, user, key: str, write: bool = False) -> Segment:
        project = self._project(project_key, user, write=write)
        return SegmentQuery.get_in_project(key, project)

    def _invalidate_referencing_flags(self, segment: Segment) -> None:
        FlagService.invalidate_many_flag_caches(
            SegmentQuery.referencing_flags(segment)
        )

    # ------------------------------------------------------------------
    # Segment CRUD
    # ------------------------------------------------------------------

    def list_segments(self, project_key: str, user):
        return SegmentQuery.list_for_project(self._project(project_key, user))

    def get_segment(self, project_key: str, user, key: str) -> Segment:
        return self._segment(project_key, user, key)

    def create_segment(self, project_key: str, user, **kwargs) -> Segment:
        project = self._project(project_key, user, write=True)
        key = kwargs.get("key")
        if SegmentQuery.exists_with_key(project, key):
            raise APIError(Error.DUPLICATE_KEY, extra=["segment", key])

        segment = SegmentQuery.create(project=project, **kwargs)
        AuditService.log(
            user=user,
            action=AuditService.CREATE,
            entity=segment,
            old_value=None,
            new_value=AuditService.snapshot(segment),
        )
        return segment

    # The lookup arg is `segment_key`, not `key`, precisely so that a caller
    # splatting serializer data containing a "key" field cannot collide with it
    # (that collision is a TypeError, i.e. a 500, not a validation error).
    def update_segment(self, project_key: str, segment_key: str, user, **kwargs) -> Segment:
        segment = self._segment(project_key, user, segment_key, write=True)
        # `key` is the handle targeting rules reference by value; letting it
        # change would orphan every rule pointing at this segment. Rejected
        # loudly rather than dropped silently, so a caller who tries is told.
        new_key = kwargs.pop("key", None)
        if new_key is not None and new_key != segment.key:
            raise APIError(Error.IMMUTABLE_FIELD, extra=["key"])

        old_snapshot = AuditService.snapshot(segment)
        for attr, value in kwargs.items():
            setattr(segment, attr, value)
        SegmentQuery.save(segment)
        self._invalidate_referencing_flags(segment)

        AuditService.log(
            user=user,
            action=AuditService.UPDATE,
            entity=segment,
            old_value=old_snapshot,
            new_value=AuditService.snapshot(segment),
        )
        return segment

    def delete_segment(self, project_key: str, key: str, user) -> None:
        segment = self._segment(project_key, user, key, write=True)
        # Refuse rather than leave rules pointing at a segment that no longer
        # exists — a dangling reference would quietly change what a flag serves.
        if SegmentQuery.is_referenced(segment):
            raise APIError(Error.SEGMENT_IN_USE)

        old_snapshot = AuditService.snapshot(segment)
        SegmentQuery.delete(segment)

        segment.pk = old_snapshot["id"]
        AuditService.log(
            user=user,
            action=AuditService.DELETE,
            entity=segment,
            old_value=old_snapshot,
            new_value=None,
        )

    # ------------------------------------------------------------------
    # Segment membership — individual users
    # ------------------------------------------------------------------

    def list_targets(self, project_key: str, key: str, user):
        return SegmentTargetQuery.list_for_segment(self._segment(project_key, user, key))

    def set_target(self, project_key: str, key: str, user, user_key: str, excluded: bool):
        """Put one user explicitly in (or out of) the segment.

        Idempotent, and moving a user from the include list to the exclude list
        is an update of the same row — a user can never be both.
        """
        segment = self._segment(project_key, user, key, write=True)
        existing = SegmentTargetQuery.find(segment, user_key)
        old_snapshot = AuditService.snapshot(existing) if existing else None

        target, created = SegmentTargetQuery.upsert(segment, user_key, excluded)
        self._invalidate_referencing_flags(segment)

        AuditService.log(
            user=user,
            action=AuditService.CREATE if created else AuditService.UPDATE,
            entity=target,
            old_value=old_snapshot,
            new_value=AuditService.snapshot(target),
        )
        return target, created

    def remove_target(self, project_key: str, key: str, user, user_key: str) -> None:
        segment = self._segment(project_key, user, key, write=True)
        target = SegmentTargetQuery.get_for_segment(segment, user_key)

        old_snapshot = AuditService.snapshot(target)
        SegmentTargetQuery.delete(target)
        self._invalidate_referencing_flags(segment)

        target.pk = old_snapshot["id"]
        AuditService.log(
            user=user,
            action=AuditService.DELETE,
            entity=target,
            old_value=old_snapshot,
            new_value=None,
        )

    # ------------------------------------------------------------------
    # Segment membership — attribute rules
    # ------------------------------------------------------------------

    def list_rules(self, project_key: str, key: str, user):
        return SegmentRuleQuery.list_for_segment(self._segment(project_key, user, key))

    @staticmethod
    def _assert_not_nested(operator) -> None:
        """Segments do not nest — see NON_SEGMENT_OPERATOR_CHOICES.

        Model `choices` are not enforced by `.create()`, so the rule is stated
        here too for callers that reach the service directly.
        """
        if operator in Operator.segment_operators():
            raise APIError(Error.INVALID_OPERATOR, extra=[operator])

    def create_rule(self, project_key: str, key: str, user, **kwargs):
        segment = self._segment(project_key, user, key, write=True)
        self._assert_not_nested(kwargs.get("operator"))
        rule = SegmentRuleQuery.create(segment=segment, **kwargs)
        self._invalidate_referencing_flags(segment)

        AuditService.log(
            user=user,
            action=AuditService.CREATE,
            entity=rule,
            old_value=None,
            new_value=AuditService.snapshot(rule),
        )
        return rule

    def update_rule(self, project_key: str, key: str, user, rule_id, **kwargs):
        segment = self._segment(project_key, user, key, write=True)
        rule = SegmentRuleQuery.get_for_segment(segment, rule_id)
        self._assert_not_nested(kwargs.get("operator", rule.operator))

        old_snapshot = AuditService.snapshot(rule)
        for attr, value in kwargs.items():
            setattr(rule, attr, value)
        SegmentRuleQuery.save(rule)
        self._invalidate_referencing_flags(segment)

        AuditService.log(
            user=user,
            action=AuditService.UPDATE,
            entity=rule,
            old_value=old_snapshot,
            new_value=AuditService.snapshot(rule),
        )
        return rule

    def delete_rule(self, project_key: str, key: str, user, rule_id) -> None:
        segment = self._segment(project_key, user, key, write=True)
        rule = SegmentRuleQuery.get_for_segment(segment, rule_id)

        old_snapshot = AuditService.snapshot(rule)
        SegmentRuleQuery.delete(rule)
        self._invalidate_referencing_flags(segment)

        rule.pk = old_snapshot["id"]
        AuditService.log(
            user=user,
            action=AuditService.DELETE,
            entity=rule,
            old_value=old_snapshot,
            new_value=None,
        )
