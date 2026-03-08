from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Add a compound index on (flag_id, priority) for the Rule table.

    Every cache-miss evaluation fetches rules ordered by priority for a single
    flag. Without this index, PostgreSQL performs a sequential scan of the
    entire rules table and then sorts — expensive once rule counts grow.

    With the index, the query becomes an index scan returning rows already in
    priority order with no sort step.
    """

    dependencies = [
        ("rules", "0002_rule_priority_rename_field_to_attribute_rename_fk"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="rule",
            index=models.Index(
                fields=["flag", "priority"],
                name="rules_rule_flag_priority_idx",
            ),
        ),
    ]
