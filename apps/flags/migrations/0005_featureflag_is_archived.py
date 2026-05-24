from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("flags", "0004_featureflag_rollout_percentage_constraint"),
    ]

    operations = [
        migrations.AddField(
            model_name="featureflag",
            name="is_archived",
            field=models.BooleanField(default=False, db_index=True),
        ),
    ]
