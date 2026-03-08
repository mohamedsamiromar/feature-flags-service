from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("flags", "0002_featureflag_name_featureflag_rollout_percentage_rename_is_active"),
        ("rules", "0001_initial"),
    ]

    operations = [
        # Rename column field -> attribute
        migrations.RenameField(
            model_name="rule",
            old_name="field",
            new_name="attribute",
        ),
        # Rename FK feature_flag -> flag
        migrations.RenameField(
            model_name="rule",
            old_name="feature_flag",
            new_name="flag",
        ),
        # Add priority
        migrations.AddField(
            model_name="rule",
            name="priority",
            field=models.IntegerField(default=0),
        ),
        # Add ordering to model options
        migrations.AlterModelOptions(
            name="rule",
            options={"ordering": ["priority"]},
        ),
    ]
