"""The committed conformance vectors must match what the engine does today.

`GET /sdk/flags/config/` ships the raw ruleset, so every server-side SDK
reimplements bucketing, rule precedence, segment membership, and prerequisite
resolution. `sdk_conformance_vectors.json` is the contract those SDKs are
checked against, and this module is what keeps it honest:

- **The staleness check.** If engine behaviour changes, the generated vectors
  change and this test fails. Regenerating is the fix, and the resulting diff is
  the reviewable record that an SDK contract moved.
- **The coverage checks.** A vector file that regenerates cleanly but exercises
  nothing would pass the first check forever. These assert the file still pins
  the properties an SDK is most likely to get wrong.
"""

import json
from pathlib import Path

import pytest
from django.conf import settings

from apps.evaluation.conformance import BUCKETING_SAMPLES
from apps.evaluation.management.commands.generate_conformance_vectors import (
    build,
    serialize,
)

VECTORS_PATH = Path(settings.BASE_DIR) / "sdk_conformance_vectors.json"


@pytest.fixture(scope="module")
def committed():
    return json.loads(VECTORS_PATH.read_text())


@pytest.fixture
def regenerated(db):
    return build()


def _case(vectors, user_id):
    for case in vectors["cases"]:
        if case["user_context"].get("user_id") == user_id:
            return case["expect"]
    raise AssertionError(f"no case for user_id={user_id!r}")


@pytest.mark.django_db
def test_committed_vectors_are_current(regenerated):
    """The committed file is exactly what the engine produces right now.

    A failure here is not necessarily a bug — it means engine behaviour or the
    fixture changed. Run `manage.py generate_conformance_vectors` and review the
    diff: it is the record of an SDK contract change.
    """
    assert serialize(regenerated) == VECTORS_PATH.read_text(), (
        "Conformance vectors are stale. Run "
        "`python manage.py generate_conformance_vectors` and review the diff."
    )


@pytest.mark.django_db
def test_generation_is_deterministic(regenerated):
    """Ids are pinned, so two runs agree byte for byte.

    Without explicit pks the fixture's rule ids would come from a database
    sequence, rule-level bucketing (salted with the rule id) would differ per
    machine, and the staleness check above could never pass.
    """
    assert serialize(build()) == serialize(regenerated)


@pytest.mark.django_db
def test_generation_leaves_no_rows_behind(regenerated):
    """The fixture is a whole organization; it is rolled back, not committed."""
    from apps.organizations.models import Organization

    assert not Organization.objects.filter(slug="conformance-vectors").exists()


# ---------------------------------------------------------------------------
# Coverage — the properties an SDK is most likely to get wrong
# ---------------------------------------------------------------------------

def test_every_operator_is_exercised(committed):
    flags = committed["config"]["flags"]
    operators = {
        rule["operator"] for flag in flags.values() for rule in flag["rules"]
    } | {
        rule["operator"]
        for segment in committed["config"]["segments"].values()
        for rule in segment["rules"]
    }

    from apps.rules.models import Operator

    assert operators == {op.value for op in Operator}


def test_gt_and_lt_fail_closed_on_a_non_numeric_operand(committed):
    """Neither may be the negation of the other: both must return false.

    If an SDK implements `lt` as `not gt`, an unusable attribute matches half
    the users it touches. This is the vector that catches it.
    """
    for user_id in ("frank", "grace"):
        expect = _case(committed, user_id)
        assert expect["op-gt"]["result"] is False
        assert expect["op-lt"]["result"] is False


def test_a_dangling_segment_never_matches_under_either_operator(committed):
    """Inverting the unknown would turn one typo into a full rollout."""
    for case in committed["cases"]:
        expect = case["expect"]
        if "closed-dangling-in-segment" not in expect:
            continue
        assert expect["closed-dangling-in-segment"]["result"] is False
        assert expect["closed-dangling-not-in-segment"]["result"] is False


def test_an_empty_segment_matches_nobody(committed):
    for case in committed["cases"]:
        if "closed-empty-segment" in case["expect"]:
            assert case["expect"]["closed-empty-segment"]["result"] is False


def test_exclusion_beats_a_matching_segment_rule(committed):
    """carol is `plan=pro`, which the segment's rule matches, and excluded."""
    expect = _case(committed, "carol")

    assert expect["op-in-segment"]["result"] is False
    assert expect["op-not-in-segment"]["result"] is True


def test_nothing_overrides_the_kill_switch(committed):
    """alice is individually targeted to `on` on a flag that is switched off."""
    assert _case(committed, "alice")["killed"]["result"] is False


def test_individual_targeting_works_in_both_directions(committed):
    assert _case(committed, "alice")["targeted-in"]["result"] is True
    assert _case(committed, "bob")["targeted-out"]["result"] is False
    # A user with no target on either flag gets neither override.
    assert _case(committed, "eve")["targeted-in"]["result"] is False
    assert _case(committed, "eve")["targeted-out"]["result"] is True


def test_the_first_matching_rule_wins_outright(committed):
    """Both rules match `plan=pro`; priority 1 serves off and must not fall
    through to priority 2, which serves on."""
    assert _case(committed, "alice")["ordered"]["result"] is False
    # A user matching neither rule reaches the fallthrough.
    assert _case(committed, "bob")["ordered"]["result"] is True


def test_prerequisites_gate_in_all_three_ways(committed):
    expect = _case(committed, "alice")

    assert expect["prereq-met"]["result"] is True
    assert expect["prereq-unmet"]["result"] is False
    # The gate is archived, so it is absent from the payload entirely.
    assert expect["prereq-archived-gate"]["result"] is False


def test_an_archived_gate_is_absent_from_the_config(committed):
    assert "archived-gate" not in committed["config"]["flags"]


def test_boundaries_are_strict(committed):
    """`gt 18` excludes 18 and `lt 65` excludes 65."""
    assert _case(committed, "heidi")["op-gt"]["result"] is False
    assert _case(committed, "ivan")["op-lt"]["result"] is False


def test_a_numeric_string_coerces(committed):
    """`{"age": "19"}` must compare as 19, not as a string."""
    assert _case(committed, "judy")["op-gt"]["result"] is True


def test_a_missing_attribute_never_matches(committed):
    """eve carries no attributes at all."""
    expect = _case(committed, "eve")

    for flag_key in ("op-eq", "op-contains", "op-in", "op-gt", "op-lt"):
        assert expect[flag_key]["result"] is False, flag_key
    # `not_in` is not an exception: a missing attribute does not match it either.
    assert expect["op-not-in"]["result"] is False


# ---------------------------------------------------------------------------
# Bucketing
# ---------------------------------------------------------------------------

def _bucket_cases(vectors):
    return [
        case for case in vectors["cases"]
        if str(case["user_context"].get("user_id", "")).startswith("bucket-")
    ]


def test_bucketing_coverage_is_dense(committed):
    """A handful of users agree by luck; a thousand do not.

    This is what catches an SDK reading the SHA-256 digest as bytes rather than
    as a hex integer — before it reaches 3% of production traffic.
    """
    assert len(_bucket_cases(committed)) == BUCKETING_SAMPLES


def test_rollout_boundaries_are_absolute(committed):
    cases = _bucket_cases(committed)

    assert all(case["expect"]["rollout-0"]["result"] is False for case in cases)
    assert all(case["expect"]["rollout-100"]["result"] is True for case in cases)


def test_a_fifty_percent_rollout_splits_roughly_in_half(committed):
    """Not an assertion about the exact partition — the file already pins that,
    user by user. This catches a fixture that stopped exercising bucketing."""
    cases = _bucket_cases(committed)
    in_bucket = sum(case["expect"]["rollout-50"]["result"] is True for case in cases)

    assert 0.4 < in_bucket / len(cases) < 0.6


def test_rule_level_bucketing_selects_a_different_slice(committed):
    """The rule id salts rule-level bucketing.

    Without the salt, a 50% rule and a 50% flag rollout hash identically and
    select exactly the same users — so a second 50% rollout would reach nobody
    new. Agreement here should look like chance, not like a copy.
    """
    cases = _bucket_cases(committed)
    agree = sum(
        case["expect"]["rollout-50"]["result"]
        == case["expect"]["rule-rollout-50"]["result"]
        for case in cases
    )

    assert agree / len(cases) < 0.75, "rule-level bucketing is not salted"
