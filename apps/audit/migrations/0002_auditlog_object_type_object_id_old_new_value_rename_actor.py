from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("audit", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # Rename actor -> user
        migrations.RenameField(
            model_name="auditlog",
            old_name="actor",
            new_name="user",
        ),
        # Remove target (replaced by object_type + object_id)
        migrations.RemoveField(
            model_name="auditlog",
            name="target",
        ),
        # Add object_type
        migrations.AddField(
            model_name="auditlog",
            name="object_type",
            field=models.CharField(default="", max_length=100),
            preserve_default=False,
        ),
        # Add object_id
        migrations.AddField(
            model_name="auditlog",
            name="object_id",
            field=models.CharField(default="", max_length=100),
            preserve_default=False,
        ),
        # Add old_value
        migrations.AddField(
            model_name="auditlog",
            name="old_value",
            field=models.JSONField(blank=True, null=True),
        ),
        # Add new_value
        migrations.AddField(
            model_name="auditlog",
            name="new_value",
            field=models.JSONField(blank=True, null=True),
        ),
        # Add ordering
        migrations.AlterModelOptions(
            name="auditlog",
            options={"ordering": ["-created_at"]},
        ),
    ]
