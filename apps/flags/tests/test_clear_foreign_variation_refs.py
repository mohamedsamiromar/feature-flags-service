"""
`manage.py clear_foreign_variation_refs` — cleanup for rows written before the
write paths checked variation ownership.

The engine already refuses to serve such a reference; this removes it from the
data, so the dashboard stops showing it and the flag's intent is visible again.
Dry run by default: it touches nothing unless told to.
"""

from io import StringIO

import pytest
from django.core.management import call_command

from apps.audit.models import AuditLog
from apps.flags.models import FeatureFlag, Variation
from apps.rules.models import Rule
from conftest import (
    EnvironmentFactory,
    EnvironmentFlagFactory,
    FeatureFlagFactory,
    ProjectFactory,
    VariationFactory,
)

SECRET = "tenant-b-secret"


def _run(*args) -> str:
    out = StringIO()
    call_command("clear_foreign_variation_refs", *args, stdout=out)
    return out.getvalue()


@pytest.fixture
def foreign(db):
    return VariationFactory(
        flag=FeatureFlagFactory(project=ProjectFactory()),
        value_type=Variation.ValueType.STRING,
        value=SECRET,
    )


@pytest.fixture
def tainted(foreign):
    """A flag whose off_variation and one rule point at another tenant's variation,
    plus a rule on the same flag pointing at the flag's own variation."""
    project = ProjectFactory()
    environment = EnvironmentFactory(project=project)
    flag = FeatureFlagFactory(
        project=project,
        flag_type=FeatureFlag.FlagType.MULTIVARIATE,
        off_variation=foreign,
    )
    EnvironmentFlagFactory(feature_flag=flag, environment=environment)
    own = VariationFactory(flag=flag)
    bad_rule = Rule.objects.create(
        flag=flag, attribute="plan", operator="eq", value="pro", serve_variation=foreign
    )
    good_rule = Rule.objects.create(
        flag=flag, attribute="plan", operator="eq", value="team", serve_variation=own
    )
    return flag, environment, bad_rule, good_rule, own


@pytest.mark.django_db
class TestDryRun:
    def test_reports_without_changing_anything(self, tainted, foreign):
        flag, environment, bad_rule, good_rule, _ = tainted
        version = environment.config_version

        out = _run()

        assert f"flag {flag.id}" in out and "off_variation" in out
        assert f"rule {bad_rule.id}" in out
        assert f"rule {good_rule.id}" not in out
        # Reports ids, never the leaked value itself.
        assert SECRET not in out

        flag.refresh_from_db()
        bad_rule.refresh_from_db()
        environment.refresh_from_db()
        assert flag.off_variation_id == foreign.id
        assert bad_rule.serve_variation_id == foreign.id
        assert environment.config_version == version
        assert not AuditLog.objects.exists()

    def test_clean_database_reports_nothing(self, db):
        assert "No foreign variation references" in _run()


@pytest.mark.django_db
class TestApply:
    def test_clears_only_foreign_references(self, tainted):
        flag, _, bad_rule, good_rule, own = tainted

        _run("--apply")

        flag.refresh_from_db()
        bad_rule.refresh_from_db()
        good_rule.refresh_from_db()
        assert flag.off_variation_id is None
        assert bad_rule.serve_variation_id is None
        assert good_rule.serve_variation_id == own.id

    def test_bumps_the_environment_version(self, tainted):
        """So polling SDKs re-download, and cached payloads are evicted."""
        _, environment, _, _, _ = tainted
        version = environment.config_version

        _run("--apply")

        environment.refresh_from_db()
        assert environment.config_version > version

    def test_each_change_is_audited(self, tainted, foreign):
        flag, _, bad_rule, _, _ = tainted

        _run("--apply")

        flag_log = AuditLog.objects.get(entity_type="featureflag", entity_id=str(flag.id))
        assert flag_log.old_value["off_variation"] == foreign.id
        assert flag_log.new_value["off_variation"] is None
        rule_log = AuditLog.objects.get(entity_type="rule", entity_id=str(bad_rule.id))
        assert rule_log.new_value["serve_variation"] is None
        # A system cleanup, not a user's edit.
        assert flag_log.user is None and rule_log.user is None

    def test_second_run_finds_nothing(self, tainted):
        _run("--apply")
        assert "No foreign variation references" in _run()
