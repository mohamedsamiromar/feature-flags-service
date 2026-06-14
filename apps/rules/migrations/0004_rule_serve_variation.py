from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("flags", "0007_variation"),
        ("rules", "0003_rule_flag_priority_index"),
    ]

    operations = [
        migrations.AddField(
            model_name="rule",
            name="serve_variation",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="rules",
                to="flags.variation",
            ),
        ),
    ]
