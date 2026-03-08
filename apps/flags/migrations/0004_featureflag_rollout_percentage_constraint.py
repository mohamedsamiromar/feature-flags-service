from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("flags", "0003_featureflag_owner_remove_environment"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="featureflag",
            constraint=models.CheckConstraint(
                check=models.Q(rollout_percentage__gte=0) & models.Q(rollout_percentage__lte=100),
                name="flags_featureflag_rollout_percentage_0_100",
            ),
        ),
    ]
