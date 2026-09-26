"""
Migration `flags.0014` keeps existing flags visible to client SDK keys.

This is what makes the feature safe to deploy: flags that browser SDKs already
read must not vanish from the bootstrap endpoint the moment the column lands,
while flags created afterwards start hidden. Replays the migration for real
rather than trusting its two operations to mean what they say.
"""

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from conftest import ProjectFactory

BEFORE = [("flags", "0013_flagprerequisite_and_more")]
AFTER = [("flags", "0014_featureflag_client_side_available")]


def _migrate(targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


@pytest.mark.django_db(transaction=True)
def test_existing_flags_stay_visible_new_flags_start_hidden():
    project = ProjectFactory()
    try:
        old_apps = _migrate(BEFORE)
        OldFlag = old_apps.get_model("flags", "FeatureFlag")
        existing = OldFlag.objects.create(project_id=project.id, name="Old", key="old")

        new_apps = _migrate(AFTER)
        NewFlag = new_apps.get_model("flags", "FeatureFlag")

        assert NewFlag.objects.get(pk=existing.pk).client_side_available is True
        created = NewFlag.objects.create(project_id=project.id, name="New", key="new")
        assert NewFlag.objects.get(pk=created.pk).client_side_available is False
    finally:
        # Leave the schema at head for every test that runs after this one.
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())
