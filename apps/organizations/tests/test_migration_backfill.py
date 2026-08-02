"""
Focused test for the personal-org backfill's slug/key de-duplication.

The end-to-end replay of migration ``organizations.0002`` is not exercised here
(the live schema has already dropped ``FeatureFlag.owner``, and no migration-
replay helper is installed). The collision-avoidance helper is the part with
real branching, so it is unit-tested directly from the migration module.
"""

import importlib

_mod = importlib.import_module("apps.organizations.migrations.0002_backfill_personal_orgs")


class TestUniqueSlug:
    def test_returns_base_when_free(self):
        taken = set()
        assert _mod._unique("acme", taken) == "acme"
        assert "acme" in taken

    def test_appends_suffix_on_collision(self):
        taken = {"acme"}
        assert _mod._unique("acme", taken) == "acme-2"

    def test_walks_past_multiple_collisions(self):
        taken = {"acme", "acme-2", "acme-3"}
        assert _mod._unique("acme", taken) == "acme-4"

    def test_records_each_result_as_taken(self):
        taken = set()
        first = _mod._unique("dup", taken)
        second = _mod._unique("dup", taken)
        assert (first, second) == ("dup", "dup-2")
        assert {"dup", "dup-2"} <= taken
