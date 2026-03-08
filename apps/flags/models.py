from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.core.models import BaseModel


class FeatureFlag(BaseModel):
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
    rollout_percentage = models.IntegerField(
        default=0,
        # Application-level: enforced by Django's full_clean() and by the
        # serializer validator below, giving a clean 400 error at the API.
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )

    class Meta:
        constraints = [
            # Database-level: a last line of defence that prevents invalid data
            # from being written even if the application layer is bypassed
            # (e.g. direct ORM calls in scripts or admin shell).
            models.CheckConstraint(
                check=models.Q(rollout_percentage__gte=0) & models.Q(rollout_percentage__lte=100),
                name="flags_featureflag_rollout_percentage_0_100",
            )
        ]

    def __str__(self):
        return self.key
