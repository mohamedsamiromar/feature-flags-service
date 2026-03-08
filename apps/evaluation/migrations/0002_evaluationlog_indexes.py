from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Add a compound index on (flag_id, evaluated_at DESC) for EvaluationLog.

    The primary analytics query pattern is:
        "Show the last N evaluations for flag X, newest first."

    Without this index, every such query is a full table scan + sort.
    With this index, PostgreSQL can satisfy the query with an index-only scan
    in reverse order — critical as the table grows to millions of rows.
    """

    dependencies = [
        ("evaluation", "0001_initial"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="evaluationlog",
            index=models.Index(
                fields=["flag", "-evaluated_at"],
                name="eval_log_flag_evaluated_at_idx",
            ),
        ),
    ]
