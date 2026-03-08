from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("audit", "0002_auditlog_object_type_object_id_old_new_value_rename_actor"),
    ]

    operations = [
        migrations.RenameField(
            model_name="auditlog",
            old_name="object_type",
            new_name="entity_type",
        ),
        migrations.RenameField(
            model_name="auditlog",
            old_name="object_id",
            new_name="entity_id",
        ),
    ]
