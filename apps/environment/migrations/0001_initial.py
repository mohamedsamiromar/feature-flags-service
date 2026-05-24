from django.conf import settings
from django.db import migrations, models
import django.core.validators
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("flags", "0006_featureflag_owner_non_nullable"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Environment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(
                    max_length=50,
                    choices=[("development", "development"), ("staging", "staging"), ("production", "production")],
                )),
                ("owner", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="environments",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={"unique_together": {("owner", "name")}},
        ),
        migrations.CreateModel(
            name="EnvironmentFlag",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_enabled", models.BooleanField(default=False)),
                ("rollout_percentage", models.IntegerField(
                    default=0,
                    validators=[
                        django.core.validators.MinValueValidator(0),
                        django.core.validators.MaxValueValidator(100),
                    ],
                )),
                ("feature_flag", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="environment_states",
                    to="flags.featureflag",
                )),
                ("environment", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="flags",
                    to="environment.environment",
                )),
            ],
            options={"unique_together": {("feature_flag", "environment")}},
        ),
    ]
