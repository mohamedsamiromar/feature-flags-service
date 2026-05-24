from django.conf import settings
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
    rollout_percentage = models.IntegerField(default=0)
    is_archived = models.BooleanField(default=False, db_index=True)

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
