from django.db import models
from apps.core.models import BaseModel
from apps.flags.models import FeatureFlag


class Operator(models.TextChoices):
    EQUALS = "eq", "Equals"
    NOT_EQUALS = "neq", "Not Equals"
    CONTAINS = "contains", "Contains"
    IN = "in", "In"
    NOT_IN = "not_in", "Not In"
    GT = "gt", "Greater Than"
    LT = "lt", "Less Than"
    # Segment operators ignore `attribute` entirely — `value` holds a segment
    # key and membership is resolved by SegmentEvaluator, not by comparing a
    # single context attribute.
    IN_SEGMENT = "in_segment", "In Segment"
    NOT_IN_SEGMENT = "not_in_segment", "Not In Segment"

    @classmethod
    def segment_operators(cls) -> set:
        return {cls.IN_SEGMENT, cls.NOT_IN_SEGMENT}


class Rule(BaseModel):
    flag = models.ForeignKey(FeatureFlag, on_delete=models.CASCADE, related_name="rules")
    # Blank for segment operators, which test membership rather than a single
    # context attribute. Required for every other operator — enforced in
    # RuleService, since the requirement depends on `operator`.
    attribute = models.CharField(max_length=100, blank=True, default="")
    operator = models.CharField(max_length=50, choices=Operator.choices)
    value = models.CharField(max_length=255)
    priority = models.IntegerField(default=0)
    serve_variation = models.ForeignKey(
        "flags.Variation",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="rules",
    )

    class Meta:
        ordering = ["priority"]
        indexes = [
            models.Index(fields=["flag", "priority"], name="rules_rule_flag_priority_idx"),
        ]

    def __str__(self):
        return f"{self.flag.key}: {self.attribute} {self.operator} {self.value}"
