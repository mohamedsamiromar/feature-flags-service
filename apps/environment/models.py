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
    project = models.ForeignKey(
        "organizations.Project",
        on_delete=models.CASCADE,
        related_name="environments",
    )

    # Monotonic counter of "something changed that affects what a flag in this
    # environment serves". Bumped wherever a flag cache is evicted — those sites
    # are already the chokepoint for exactly that question, so this adds no new
    # invalidation surface.
    #
    # It is the ETag for GET /sdk/flags/config/, which lets a polling SDK get a
    # 304 with an empty body instead of a payload it already has, and it is what
    # makes SSE tractable later: a stream event carries the new version, and an
    # SDK that sees a gap re-fetches rather than trusting a delta it cannot check.
    #
    # Bumps go through `EnvironmentQuery.bump_config_versions`, which uses an
    # atomic F() update — a Python-side increment would lose writes under
    # concurrent mutations.
    config_version = models.PositiveBigIntegerField(default=1)

    # Secure mode: client SDK keys (which ship to browsers) must present
    # HMAC-SHA256(secure_mode_secret, user_id), computed by the customer's own
    # backend, so a browser cannot evaluate flags as someone else's user id.
    # See `SecureModeService`.
    secure_mode = models.BooleanField(default=False)
    # Stored raw, unlike SDK keys: the server has to recompute the HMAC, so a
    # hash of the secret would be useless. Never serialized with the
    # environment, and redacted from audit snapshots (AuditService).
    secure_mode_secret = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        unique_together = ("project", "name")

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