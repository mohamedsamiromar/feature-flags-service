from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("flags", "0007_variation"),
    ]

    operations = [
        migrations.AddField(
            model_name="featureflag",
            name="flag_type",
            field=models.CharField(
                choices=[("boolean", "Boolean"), ("multivariate", "Multivariate")],
                default="boolean",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="featureflag",
            name="off_variation",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="off_variation_flags",
                to="flags.variation",
            ),
        ),
        migrations.AddField(
            model_name="featureflag",
            name="fallthrough_variation",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="fallthrough_variation_flags",
                to="flags.variation",
            ),
        ),
    ]
