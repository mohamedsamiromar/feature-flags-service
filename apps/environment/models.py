from django.conf import settings
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from apps.core.models import BaseModel
from apps.flags.models import FeatureFlag
from enum import Enum


class Environment(BaseModel):
    class EnvironmentName(Enum):
        DEVELOPMENT = "development"
        STAGING = "staging"
        PRODUCTION = "production"

    name = models.CharField(max_length=50, choices=[(tag.value, tag.value) for tag in EnvironmentName])
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="environments",
    )

    class Meta:
        unique_together = ("owner", "name")

    def __str__(self):
        return self.name


class EnvironmentFlag(BaseModel):
    feature_flag = models.ForeignKey(
        FeatureFlag,
        on_delete=models.CASCADE,
        related_name="environment_states",
    )

    environment = models.ForeignKey(
        Environment,
        on_delete=models.CASCADE,
        related_name="flags",
    )

    is_enabled = models.BooleanField(default=False)

    rollout_percentage = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )

    class Meta:
        unique_together = ("feature_flag", "environment")