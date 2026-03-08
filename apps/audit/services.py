from __future__ import annotations

from django.forms.models import model_to_dict

from apps.audit.models import AuditLog


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
        return AuditLog.objects.create(
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

    @staticmethod
    def snapshot(instance) -> dict:
        """
        Return a JSON-serialisable dict of a model instance's field values.

        Excludes auto-managed fields (created_at, updated_at) that are not
        meaningful for diffing purposes.
        """
        data = model_to_dict(instance)
        # model_to_dict omits auto fields; add pk explicitly for traceability
        data["id"] = instance.pk
        return data
