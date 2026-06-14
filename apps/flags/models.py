from django.conf import settings
from django.db import models
from apps.core.models import BaseModel


class FeatureFlag(BaseModel):
    class FlagType(models.TextChoices):
        BOOLEAN = "boolean", "Boolean"
        MULTIVARIATE = "multivariate", "Multivariate"

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="flags",
        db_index=True,
    )
    name = models.CharField(max_length=150)
    key = models.CharField(max_length=150, unique=True, db_index=True)
    description = models.TextField(blank=True)
    is_enabled = models.BooleanField(default=False)
    rollout_percentage = models.IntegerField(default=0)
    is_archived = models.BooleanField(default=False, db_index=True)
    flag_type = models.CharField(
        max_length=20,
        choices=FlagType.choices,
        default=FlagType.BOOLEAN,
    )
    off_variation = models.ForeignKey(
        "Variation",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="off_variation_flags",
    )
    fallthrough_variation = models.ForeignKey(
        "Variation",
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="fallthrough_variation_flags",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["owner", "key"], name="unique_flag_per_owner"),
            models.CheckConstraint(
                check=models.Q(rollout_percentage__gte=0) & models.Q(rollout_percentage__lte=100),
                name="flags_featureflag_rollout_percentage_0_100",
            ),
        ]

    def __str__(self):
        return self.key


class Variation(BaseModel):
    class ValueType(models.TextChoices):
        BOOLEAN = "boolean", "Boolean"
        STRING = "string", "String"
        NUMBER = "number", "Number"
        JSON = "json", "JSON"

    flag = models.ForeignKey(
        FeatureFlag,
        on_delete=models.CASCADE,
        related_name="variations",
    )
    name = models.CharField(max_length=100)
    value_type = models.CharField(max_length=20, choices=ValueType.choices)
    value = models.JSONField()

    class Meta:
        unique_together = [("flag", "name")]

    def __str__(self):
        return f"{self.flag.key}/{self.name}"
