from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Add two indexes to AuditLog:

    1. (entity_type, entity_id) — supports "show all changes to flag X" queries.
       Without this, filtering by entity requires a sequential scan of the
       entire audit log table.

    2. (user_id, created_at DESC) — supports the primary user-facing query
       "show my recent audit actions, newest first", which is what
       AuditLogViewSet.get_queryset() filters on.
    """

    dependencies = [
        ("audit", "0003_rename_object_to_entity"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(
                fields=["entity_type", "entity_id"],
                name="audit_log_entity_type_id_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="auditlog",
            index=models.Index(
                fields=["user", "-created_at"],
                name="audit_log_user_created_at_idx",
            ),
        ),
    ]
