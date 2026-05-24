from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("environment", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="SDKKey",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("name", models.CharField(max_length=100)),
                ("prefix", models.CharField(max_length=16)),
                ("hashed_key", models.CharField(max_length=64, unique=True, db_index=True)),
                ("key_type", models.CharField(
                    max_length=10,
                    choices=[("server", "Server"), ("client", "Client")],
                    default="server",
                )),
                ("is_active", models.BooleanField(default=True, db_index=True)),
                ("last_used_at", models.DateTimeField(null=True, blank=True)),
                ("environment", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="sdk_keys",
                    to="environment.environment",
                )),
            ],
            options={"abstract": False},
        ),
    ]
