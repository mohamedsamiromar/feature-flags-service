from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("flags", "0006_featureflag_owner_non_nullable"),
    ]

    operations = [
        migrations.CreateModel(
            name="Variation",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=100)),
                (
                    "value_type",
                    models.CharField(
                        choices=[
                            ("boolean", "Boolean"),
                            ("string", "String"),
                            ("number", "Number"),
                            ("json", "JSON"),
                        ],
                        max_length=20,
                    ),
                ),
                ("value", models.JSONField()),
                (
                    "flag",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="variations",
                        to="flags.featureflag",
                    ),
                ),
            ],
            options={
                "unique_together": {("flag", "name")},
            },
        ),
    ]
