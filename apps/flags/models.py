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

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['owner', 'key'], name='unique_flag_per_owner')
        ]

    def __str__(self):
        return self.key
