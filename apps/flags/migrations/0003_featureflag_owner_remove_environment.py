from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("flags", "0002_featureflag_name_featureflag_rollout_percentage_rename_is_active"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # 1. Add owner (nullable temporarily so existing rows are handled)
        migrations.AddField(
            model_name="featureflag",
            name="owner",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="flags",
                to=settings.AUTH_USER_MODEL,
                db_index=True,
            ),
            preserve_default=False,
        ),
        # 2. Drop environment FK from FeatureFlag
        migrations.RemoveField(
            model_name="featureflag",
            name="environment",
        ),
        # 3. Drop the Environment table
        migrations.DeleteModel(
            name="Environment",
        ),
        # 4. Add db_index on key (unique already provides one, explicit for clarity)
        migrations.AlterField(
            model_name="featureflag",
            name="key",
            field=models.CharField(max_length=150, unique=True, db_index=True),
        ),
    ]
