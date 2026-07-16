from django.db import migrations, models


class Migration(migrations.Migration):
    """
    Widen EvaluationLog.result from BooleanField to JSONField.

    Multivariate flags (F-07) serve strings, numbers, and JSON objects, but the
    column only accepted booleans — so every non-boolean evaluation raised in
    the Celery task and was silently dropped.

    PostgreSQL cannot cast boolean to jsonb implicitly, so the type change is
    expressed as raw SQL with an explicit USING clause and paired with a
    state-only AlterField.
    """

    dependencies = [
        ("evaluation", "0002_evaluationlog_indexes"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        'ALTER TABLE "evaluation_evaluationlog" '
                        'ALTER COLUMN "result" TYPE jsonb USING to_jsonb("result");'
                    ),
                    reverse_sql=(
                        'ALTER TABLE "evaluation_evaluationlog" '
                        'ALTER COLUMN "result" TYPE boolean '
                        'USING ("result" #>> \'{}\')::boolean;'
                    ),
                ),
            ],
            state_operations=[
                migrations.AlterField(
                    model_name="evaluationlog",
                    name="result",
                    field=models.JSONField(),
                ),
            ],
        ),
    ]
