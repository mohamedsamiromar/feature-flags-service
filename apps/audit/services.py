from __future__ import annotations

from django.forms.models import model_to_dict

from apps.audit.models import AuditLog
from apps.audit.queries import AuditQuery


class AuditService:
    """
    Centralized audit-log writer.

    Usage:
        AuditService.log(
            user=request.user,
            action="create",
            entity=flag_instance,          # any Django model instance
            old_value=None,                # dict or None (before state)
            new_value={"key": "dark-mode", "is_enabled": True},
        )

    The entity_type is derived from the model's verbose name, and entity_id
    from the instance's primary key, so callers never need to hard-code strings.
    """

    # Actions — use these constants instead of raw strings so typos are caught
    # at import time rather than at query time.
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    ARCHIVE = "archive"
    UNARCHIVE = "unarchive"
    TOGGLE = "toggle"
    ROLLBACK = "rollback"
    REVOKE = "revoke"
    ROTATE = "rotate"
    ACCEPT = "accept"
    DECLINE = "decline"

    # Fields that must never be copied into an audit entry, keyed by
    # `Model._meta.model_name`. A registry rather than a per-call argument: an
    # argument is something a future caller can forget, and forgetting here
    # writes the secret to a second table with different access rules.
    #
    # `SDKKey.hashed_key` is the value `SDKKeyAuthentication` looks a key up by.
    # It is not the raw credential, but the whole point of hash-only storage is
    # that the digest lives in exactly one place.
    REDACTED_FIELDS = {
        "sdkkey": {"hashed_key"},
    }

    @classmethod
    def log(
        cls,
        *,
        user,
        action: str,
        entity,
        old_value: dict | None = None,
        new_value: dict | None = None,
    ) -> AuditLog:
        """
        Create and persist a single audit log entry.

        Args:
            user:       The authenticated user performing the action.
            action:     One of AuditService.CREATE / UPDATE / DELETE.
            entity:     The Django model instance being acted upon.
            old_value:  Snapshot of the entity *before* the mutation (None for creates).
            new_value:  Snapshot of the entity *after* the mutation (None for deletes).

        Returns:
            The newly created AuditLog instance.
        """
        return AuditQuery.create(
            user=user,
            action=action,
            entity_type=entity._meta.model_name,
            entity_id=str(entity.pk),
            old_value=old_value,
            new_value=new_value,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @classmethod
    def log_delete(cls, *, user, entity, old_value: dict) -> AuditLog:
        """Log a DELETE for an instance that has already been deleted.

        ``Model.delete()`` sets ``instance.pk`` to None, so logging afterwards
        would record ``entity_id="None"`` and detach the entry from the row it
        describes. The pk is restored from the snapshot taken before the delete.

        Every delete goes through here rather than repeating that restore at
        each call site — the failure is silent, and an audit entry that cannot
        be traced to its row is worse than no entry at all.
        """
        entity.pk = old_value["id"]
        return cls.log(
            user=user, action=cls.DELETE, entity=entity, old_value=old_value
        )

    @classmethod
    def snapshot(cls, instance) -> dict:
        """
        Return a JSON-serialisable dict of a model instance's field values.

        Excludes auto-managed fields (created_at, updated_at) that are not
        meaningful for diffing purposes, and anything listed in
        ``REDACTED_FIELDS`` for this model.
        """
        data = model_to_dict(instance)
        # model_to_dict omits auto fields; add pk explicitly for traceability
        data["id"] = instance.pk
        for field in cls.REDACTED_FIELDS.get(instance._meta.model_name, ()):
            data.pop(field, None)
        return data
