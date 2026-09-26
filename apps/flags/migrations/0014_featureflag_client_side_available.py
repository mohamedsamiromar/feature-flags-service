from django.db import migrations, models


class Migration(migrations.Migration):
    """Add `FeatureFlag.client_side_available`.

    Two steps on purpose. Flags that exist today are already served to client
    SDK keys, and browser SDKs in production depend on that, so the column is
    added with `default=True` — every existing row starts visible and nothing
    live breaks on deploy. The default is then switched to False, so every flag
    created from here on is hidden from client keys until someone opts it in.
    """

    dependencies = [
        ("flags", "0013_flagprerequisite_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="featureflag",
            name="client_side_available",
            field=models.BooleanField(default=True),
        ),
        migrations.AlterField(
            model_name="featureflag",
            name="client_side_available",
            field=models.BooleanField(default=False),
        ),
    ]
