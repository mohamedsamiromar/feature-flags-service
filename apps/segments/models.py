from django.db import models

from apps.core.models import BaseModel
from apps.rules.models import Operator


class Segment(BaseModel):
    """A reusable, named group of users, defined once and referenced by many flags.

    Without segments, "our beta testers" has to be re-expressed as a targeting
    rule on every flag that cares, and changing who counts as a beta tester
    means editing all of them. A segment moves that definition to one place: a
    flag rule then says "is in segment `beta-testers`" and the membership logic
    lives here.

    A segment is scoped to a project, so it can be referenced by any flag in
    that project and by nothing outside it.
    """

    project = models.ForeignKey(
        "organizations.Project",
        on_delete=models.CASCADE,
        related_name="segments",
    )
    name = models.CharField(max_length=150)
    key = models.SlugField(max_length=150)
    description = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "key"],
                name="unique_segment_per_project",
            ),
        ]

    def __str__(self):
        return f"{self.project.key}/{self.key}"


class SegmentTarget(BaseModel):
    """An individually named user, explicitly in or out of a segment.

    `excluded=False` is the include list, `excluded=True` the exclude list.
    Exclusion wins over everything else (see ``SegmentEvaluator``), which makes
    it a reliable way to carve one person out of an otherwise rule-defined
    group.
    """

    segment = models.ForeignKey(
        Segment,
        on_delete=models.CASCADE,
        related_name="targets",
    )
    user_key = models.CharField(max_length=255)
    excluded = models.BooleanField(default=False)

    class Meta:
        constraints = [
            # One verdict per user per segment: a user cannot be both included
            # and excluded. The unique index also serves the lookup.
            models.UniqueConstraint(
                fields=["segment", "user_key"],
                name="unique_segment_target_per_user",
            ),
        ]

    def __str__(self):
        verdict = "excluded" if self.excluded else "included"
        return f"{self.segment.key}:{self.user_key} ({verdict})"


# Segments deliberately cannot nest: a segment rule may use every operator
# EXCEPT the segment ones. Allowing `in_segment` inside a segment would mean
# recursive membership resolution (and cycle detection, and resolving the
# referenced segment into the cached payload) — none of which SegmentEvaluator
# does. Left open, a `not_in_segment` segment rule resolves against an empty
# segment map and matches *everyone*, silently making the segment universal.
NON_SEGMENT_OPERATOR_CHOICES = [
    (op.value, op.label) for op in Operator if op not in Operator.segment_operators()
]


class SegmentRule(BaseModel):
    """An attribute condition that pulls users into a segment.

    Reuses ``rules.Operator`` so segment conditions and flag targeting rules
    speak the same operator language — minus the segment operators themselves,
    since segments do not nest (see NON_SEGMENT_OPERATOR_CHOICES).
    """

    segment = models.ForeignKey(
        Segment,
        on_delete=models.CASCADE,
        related_name="rules",
    )
    attribute = models.CharField(max_length=100)
    operator = models.CharField(max_length=50, choices=NON_SEGMENT_OPERATOR_CHOICES)
    value = models.CharField(max_length=255)

    class Meta:
        indexes = [
            models.Index(fields=["segment"], name="segment_rule_segment_idx"),
        ]

    def __str__(self):
        return f"{self.segment.key}: {self.attribute} {self.operator} {self.value}"
