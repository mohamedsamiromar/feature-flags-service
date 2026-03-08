from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("flags", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="featureflag",
            name="name",
            field=models.CharField(default="", max_length=150),
            preserve_default=False,
        ),
        migrations.RenameField(
            model_name="featureflag",
            old_name="is_active",
            new_name="is_enabled",
        ),
        migrations.AddField(
            model_name="featureflag",
            name="rollout_percentage",
            field=models.IntegerField(default=0),
        ),
    ]
