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


class Rule(BaseModel):
    flag = models.ForeignKey(FeatureFlag, on_delete=models.CASCADE, related_name="rules")
    attribute = models.CharField(max_length=100)
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
