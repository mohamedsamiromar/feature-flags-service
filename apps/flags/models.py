from django.conf import settings
from django.db import models
from apps.core.models import BaseModel


class FeatureFlag(BaseModel):
    class FlagType(models.TextChoices):
        BOOLEAN = "boolean", "Boolean"
        MULTIVARIATE = "multivariate", "Multivariate"

    project = models.ForeignKey(
        "organizations.Project",
        on_delete=models.CASCADE,
        related_name="flags",
        db_index=True,
    )
    name = models.CharField(max_length=150)
    key = models.CharField(max_length=150, db_index=True)
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
            models.UniqueConstraint(fields=["project", "key"], name="unique_flag_per_project"),
            models.CheckConstraint(
                check=models.Q(rollout_percentage__gte=0) & models.Q(rollout_percentage__lte=100),
                name="flags_featureflag_rollout_percentage_0_100",
            ),
        ]

    def __str__(self):
        return self.key


class FlagVersion(BaseModel):
    """An immutable snapshot of a flag's configuration at a point in time.

    A new row is appended every time the flag's config is created, updated, or
    rolled back. `snapshot` holds the restorable config (see
    ``FlagService._snapshot_config``); rolling back reads a prior snapshot and
    appends a fresh version rather than mutating history.
    """

    class ChangeAction(models.TextChoices):
        CREATE = "create", "Create"
        UPDATE = "update", "Update"
        ROLLBACK = "rollback", "Rollback"

    flag = models.ForeignKey(
        FeatureFlag,
        on_delete=models.CASCADE,
        related_name="versions",
    )
    version_no = models.PositiveIntegerField()
    snapshot = models.JSONField()
    change_action = models.CharField(
        max_length=20,
        choices=ChangeAction.choices,
    )
    # The version this one was rolled back from (null unless change_action is
    # ``rollback``), so the history reads "v5 is a rollback to v2".
    source_version_no = models.PositiveIntegerField(null=True, blank=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="flag_versions",
    )

    class Meta:
        ordering = ["-version_no"]
        constraints = [
            models.UniqueConstraint(
                fields=["flag", "version_no"],
                name="unique_version_per_flag",
            ),
        ]
        indexes = [
            models.Index(fields=["flag", "-version_no"], name="flag_version_flag_no_idx"),
        ]

    def __str__(self):
        return f"{self.flag.key} v{self.version_no}"


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


class FlagTarget(BaseModel):
    """An individual user pinned to a specific variation of a flag.

    This is the "individual targeting" layer: it overrides targeting rules and
    the percentage rollout for one named user, so a specific person can be let
    into a feature early (target the `true`/fallthrough variation) or held out
    of it (target the `false`/off variation) without touching anyone else.

    `user_key` is the same identifier the SDK sends as `user_id` in its
    evaluation context. A user may hold at most one target per flag, so the
    override is always unambiguous.

    Targets do not override the kill switch: a flag that is off in an
    environment serves the off variation to everyone, targets included.
    """

    flag = models.ForeignKey(
        FeatureFlag,
        on_delete=models.CASCADE,
        related_name="targets",
    )
    variation = models.ForeignKey(
        Variation,
        on_delete=models.CASCADE,
        related_name="targets",
    )
    user_key = models.CharField(max_length=255)

    class Meta:
        constraints = [
            # Doubles as the lookup index: the unique index on (flag, user_key)
            # serves both the per-flag prefetch and the single-user lookup, so
            # no extra Index/db_index is needed.
            models.UniqueConstraint(
                fields=["flag", "user_key"],
                name="unique_target_per_flag_user",
            ),
        ]

    def __str__(self):
        return f"{self.flag.key}:{self.user_key}→{self.variation.name}"
