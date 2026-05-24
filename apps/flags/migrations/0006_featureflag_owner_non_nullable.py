from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    """
    Migration 0003 added owner as nullable so existing rows could be populated.
    This migration makes it non-nullable to match the current model definition.
    Any remaining NULL owner rows are deleted before the constraint is applied.
    """

    dependencies = [
        ("flags", "0005_featureflag_is_archived"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # Remove rows with no owner (shouldn't exist in practice; safety net)
        migrations.RunSQL(
            sql='DELETE FROM "flags_featureflag" WHERE owner_id IS NULL',
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AlterField(
            model_name="featureflag",
            name="owner",
            field=models.ForeignKey(
                db_index=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="flags",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
