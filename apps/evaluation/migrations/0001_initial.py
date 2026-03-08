from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("flags", "0003_featureflag_owner_remove_environment"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="EvaluationLog",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("result", models.BooleanField()),
                ("context_data", models.JSONField()),
                ("evaluated_at", models.DateTimeField(auto_now_add=True)),
                (
                    "flag",
                    models.ForeignKey(
                        db_index=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="evaluations",
                        to="flags.featureflag",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        db_index=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-evaluated_at"],
            },
        ),
    ]
