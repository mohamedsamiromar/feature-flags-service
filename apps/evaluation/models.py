from django.conf import settings
from django.db import models

from apps.flags.models import FeatureFlag


class EvaluationLog(models.Model):
    flag = models.ForeignKey(
        FeatureFlag,
        on_delete=models.CASCADE,
        related_name="evaluations",
        db_index=True,
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        db_index=True,
    )
    # The served variation value: boolean, string, number, or JSON object.
    result = models.JSONField()
    context_data = models.JSONField()
    evaluated_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-evaluated_at"]
        indexes = [
            models.Index(
                fields=["flag", "-evaluated_at"],
                name="eval_log_flag_evaluated_at_idx",
            ),
        ]

    def __str__(self):
        return f"{self.flag.key} → {self.result} ({self.evaluated_at})"
