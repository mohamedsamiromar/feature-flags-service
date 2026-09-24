"""Generated conformance vectors for SDKs that evaluate flags in-process.

`GET /sdk/flags/config/` ships the raw ruleset, which means every server-side
SDK reimplements bucketing, rule precedence, segment membership, and
prerequisite resolution. Any divergence between an SDK and this engine serves
the wrong value to real users, silently. This module is the mitigation, and
SDK_CONFIG_SPEC.md §6 is explicit that it is not optional.

Two rules make it work:

**The vectors are produced by the engine, never hand-written.** The fixture is
built, `FlagEvaluationService` is run over every case, and its answers become
the expectations. They cannot drift from the implementation, because the
implementation is what writes them.

**Every id is explicit.** Bucketing is salted with the rule id, so a fixture
whose ids came from a database sequence would produce different expectations on
every machine, and the regenerate-and-diff test in CI could never pass. The
fixture pins each pk in a high, reserved band, and the whole thing is built
inside a transaction that is rolled back.

An SDK is conformant when it reproduces every case. That is the answer to the
objection that made local evaluation risky in the first place.
"""

from apps.environment.models import Environment, EnvironmentFlag
from apps.evaluation.services import CONFIG_FORMAT_VERSION, FlagEvaluationService
from apps.flags.models import FeatureFlag, FlagPrerequisite, FlagTarget, Variation
from apps.organizations.models import Organization, Project
from apps.rules.models import Operator, Rule
from apps.segments.models import Segment, SegmentRule, SegmentTarget

# Reserved pk band. High enough that a fixture built inside a rolled-back
# transaction on a populated database cannot collide with real rows.
BASE = 900_000

# One band per model, so a flag's own index `n` addresses its rows across all of
# them without any two bands ever overlapping. Interleaving them (flag n at
# BASE+n, its variations at BASE+n+100) collides as soon as n reaches 100 — and
# collides silently, because the ids are only wrong, never missing.
SEGMENT = BASE + 1_000
SEGMENT_TARGET = BASE + 2_000
SEGMENT_RULE = BASE + 3_000
FLAG = BASE + 4_000
ENV_FLAG = BASE + 5_000
# Two variations per flag, so this band strides by 10 to leave room.
VARIATION = BASE + 6_000
RULE = BASE + 8_000
FLAG_TARGET = BASE + 9_000
PREREQUISITE = BASE + 10_000

# How many synthetic user ids probe the rollout partition. Dense on purpose: an
# SDK that reads the SHA-256 digest as bytes instead of a hex integer, or that
# salts the flag-level rollout, agrees with the server on a handful of users by
# luck and fails here immediately, rather than on 3% of production traffic.
BUCKETING_SAMPLES = 1000


def build_fixture() -> Environment:
    """Create the scenario the vectors are generated from.

    Exercises every operator, every segment shape, both rollout levels, a
    prerequisite chain, and every fail-closed case in SDK_CONFIG_SPEC.md §4.5.

    Callers are responsible for the transaction — see the management command.
    """
    org = Organization.objects.create(
        pk=BASE, name="Conformance", slug="conformance-vectors"
    )
    project = Project.objects.create(
        pk=BASE, organization=org, name="Conformance", key="conformance-vectors"
    )
    environment = Environment.objects.create(
        pk=BASE, project=project, name="production", config_version=1
    )

    _build_segments(project)
    flags = _build_flags(project, environment)
    _build_prerequisites(flags)
    return environment


def _build_segments(project: Project) -> None:
    beta = Segment.objects.create(pk=SEGMENT, project=project, key="beta", name="Beta")
    SegmentTarget.objects.create(
        pk=SEGMENT_TARGET, segment=beta, user_key="alice", excluded=False
    )
    # Excluded *and* matched by the rule below — exclusion must win.
    SegmentTarget.objects.create(
        pk=SEGMENT_TARGET + 1, segment=beta, user_key="carol", excluded=True
    )
    SegmentRule.objects.create(
        pk=SEGMENT_RULE, segment=beta, attribute="plan",
        operator=Operator.EQUALS, value="pro",
    )

    # No targets and no rules: must match nobody, never everybody.
    Segment.objects.create(pk=SEGMENT + 1, project=project, key="empty", name="Empty")


def _variations(flag: FeatureFlag, n: int):
    on = Variation.objects.create(
        pk=VARIATION + n * 10, flag=flag, name="on",
        value_type=Variation.ValueType.BOOLEAN, value=True,
    )
    off = Variation.objects.create(
        pk=VARIATION + n * 10 + 1, flag=flag, name="off",
        value_type=Variation.ValueType.BOOLEAN, value=False,
    )
    flag.off_variation, flag.fallthrough_variation = off, on
    flag.save(update_fields=["off_variation", "fallthrough_variation"])
    return on, off


def _flag(project, environment, n, key, *, is_enabled=True, rollout=100):
    """A boolean flag with on/off variations, present in `environment`.

    `n` is the flag's index and addresses its rows in every band.
    """
    flag = FeatureFlag.objects.create(
        pk=FLAG + n, project=project, key=key, name=key, is_enabled=True
    )
    on, off = _variations(flag, n)
    EnvironmentFlag.objects.create(
        pk=ENV_FLAG + n,
        feature_flag=flag,
        environment=environment,
        is_enabled=is_enabled,
        rollout_percentage=rollout,
    )
    return flag, on, off


def _build_flags(project: Project, environment: Environment) -> dict:
    flags = {}

    # --- one flag per operator ---
    #
    # Each sits at rollout 0, so `True` means "the rule matched and served its
    # variation" and `False` means "it did not". At 100% the fallthrough would
    # also serve `on`, and every case in the file would read True whether the
    # operator worked or not — which is exactly what the first draft of these
    # vectors did.
    for i, (key, attribute, operator, value) in enumerate([
        ("op-eq", "plan", Operator.EQUALS, "pro"),
        ("op-neq", "plan", Operator.NOT_EQUALS, "pro"),
        ("op-contains", "email", Operator.CONTAINS, "@example.com"),
        ("op-in", "country", Operator.IN, "EG, US, DE"),
        ("op-not-in", "country", Operator.NOT_IN, "EG, US, DE"),
        ("op-gt", "age", Operator.GT, "18"),
        ("op-lt", "age", Operator.LT, "65"),
        ("op-in-segment", "", Operator.IN_SEGMENT, "beta"),
        ("op-not-in-segment", "", Operator.NOT_IN_SEGMENT, "beta"),
        # Fail-closed: no segment named `ghost` exists. Both operators must
        # match nobody — inverting the unknown would turn one dangling
        # reference into a full rollout.
        ("closed-dangling-in-segment", "", Operator.IN_SEGMENT, "ghost"),
        ("closed-dangling-not-in-segment", "", Operator.NOT_IN_SEGMENT, "ghost"),
        # Fail-closed: an empty segment matches nobody under `in_segment` and
        # must not match everybody under `not_in_segment` either.
        ("closed-empty-segment", "", Operator.IN_SEGMENT, "empty"),
    ]):
        flag, on, off = _flag(project, environment, i, key, rollout=0)
        Rule.objects.create(
            pk=RULE + i, flag=flag, attribute=attribute, operator=operator,
            value=value, priority=1, rollout_percentage=100, serve_variation=on,
        )
        flags[key] = flag

    # --- precedence: kill switch, individual targets, first-match-wins ---
    killed, _, _ = _flag(project, environment, 20, "killed", is_enabled=False)
    # A target on a killed flag must still not get it — nothing overrides the
    # kill switch.
    FlagTarget.objects.create(
        pk=FLAG_TARGET, flag=killed, user_key="alice",
        variation=killed.fallthrough_variation,
    )
    flags["killed"] = killed

    # Both directions, on separate flags. One flag serving `on` to alice and
    # `off` to bob cannot be checked: at rollout 0 bob's target and the
    # fallthrough resolve to the same variation, so the vector would pass for an
    # SDK that ignores targets entirely.
    targeted_in, on, _ = _flag(project, environment, 21, "targeted-in", rollout=0)
    FlagTarget.objects.create(
        pk=FLAG_TARGET + 1, flag=targeted_in, user_key="alice", variation=on
    )
    flags["targeted-in"] = targeted_in

    targeted_out, _, off = _flag(project, environment, 23, "targeted-out", rollout=100)
    FlagTarget.objects.create(
        pk=FLAG_TARGET + 3, flag=targeted_out, user_key="bob", variation=off
    )
    flags["targeted-out"] = targeted_out

    ordered, on, off = _flag(project, environment, 22, "ordered", rollout=100)
    # Priority 1 matches `plan=pro` and serves OFF. A later rule matching the
    # same user must never be reached: first match wins outright.
    Rule.objects.create(
        pk=RULE + 20, flag=ordered, attribute="plan", operator=Operator.EQUALS,
        value="pro", priority=1, rollout_percentage=100, serve_variation=off,
    )
    Rule.objects.create(
        pk=RULE + 21, flag=ordered, attribute="plan", operator=Operator.EQUALS,
        value="pro", priority=2, rollout_percentage=100, serve_variation=on,
    )
    flags["ordered"] = ordered

    # --- rollout boundaries ---
    for i, (key, rollout) in enumerate([
        ("rollout-0", 0), ("rollout-100", 100), ("rollout-50", 50),
    ]):
        flag, _, _ = _flag(project, environment, 30 + i, key, rollout=rollout)
        flags[key] = flag

    # Rule-level rollout, salted with the rule id so it selects a different
    # slice than the flag-level rollout at the same percentage.
    rule_rollout, on, _ = _flag(project, environment, 40, "rule-rollout-50")
    Rule.objects.create(
        pk=RULE + 30, flag=rule_rollout, attribute="plan", operator=Operator.EQUALS,
        value="pro", priority=1, rollout_percentage=50, serve_variation=on,
    )
    flags["rule-rollout-50"] = rule_rollout

    # --- multivariate: value alone cannot identify a variation ---
    multi = FeatureFlag.objects.create(
        pk=FLAG + 50, project=project, key="multivariate", name="multivariate",
        is_enabled=True, flag_type=FeatureFlag.FlagType.MULTIVARIATE,
    )
    control = Variation.objects.create(
        pk=VARIATION + 500, flag=multi, name="control",
        value_type=Variation.ValueType.STRING, value="control",
    )
    treatment = Variation.objects.create(
        pk=VARIATION + 501, flag=multi, name="treatment",
        value_type=Variation.ValueType.STRING, value="treatment",
    )
    multi.off_variation, multi.fallthrough_variation = control, treatment
    multi.save(update_fields=["off_variation", "fallthrough_variation"])
    EnvironmentFlag.objects.create(
        pk=ENV_FLAG + 50, feature_flag=multi, environment=environment,
        is_enabled=True, rollout_percentage=100,
    )
    flags["multivariate"] = multi

    # --- legacy: no variations configured at all, serves raw true/false ---
    legacy = FeatureFlag.objects.create(
        pk=FLAG + 60, project=project, key="legacy-no-variations",
        name="legacy", is_enabled=True,
    )
    EnvironmentFlag.objects.create(
        pk=ENV_FLAG + 60, feature_flag=legacy, environment=environment,
        is_enabled=True, rollout_percentage=100,
    )
    flags["legacy-no-variations"] = legacy

    # --- prerequisites, on flags of their own ---
    #
    # Not layered onto an operator flag: a gated `op-neq` is off for everyone,
    # so the neq operator would never actually be exercised by any case.
    for i, key in enumerate(("prereq-met", "prereq-unmet", "prereq-archived-gate")):
        flag, _, _ = _flag(project, environment, 70 + i, key, rollout=100)
        flags[key] = flag

    # A gate that exists but is archived, so it is absent from the config
    # payload entirely. The dependent must stay off: a prerequisite naming a
    # flag an SDK cannot find is unresolvable, and unresolvable is always off.
    archived_gate = FeatureFlag.objects.create(
        pk=FLAG + 90, project=project, key="archived-gate", name="archived-gate",
        is_enabled=True, is_archived=True,
    )
    _variations(archived_gate, 90)
    EnvironmentFlag.objects.create(
        pk=ENV_FLAG + 90, feature_flag=archived_gate, environment=environment,
        is_enabled=True, rollout_percentage=100,
    )
    flags["archived-gate"] = archived_gate

    return flags


def _build_prerequisites(flags: dict) -> None:
    """A met gate, an unmet gate, and a gate absent from the payload."""
    gate_on = flags["rollout-100"]
    gate_off = flags["killed"]
    gate_archived = flags["archived-gate"]

    FlagPrerequisite.objects.create(
        pk=PREREQUISITE, flag=flags["prereq-met"], prerequisite_flag=gate_on,
        required_variation=gate_on.fallthrough_variation,
    )
    # The gate is killed, so it serves its OFF variation — not the required one.
    # The dependent must be off for everyone, its own 100% rollout included.
    FlagPrerequisite.objects.create(
        pk=PREREQUISITE + 1, flag=flags["prereq-unmet"], prerequisite_flag=gate_off,
        required_variation=gate_off.fallthrough_variation,
    )
    FlagPrerequisite.objects.create(
        pk=PREREQUISITE + 2, flag=flags["prereq-archived-gate"],
        prerequisite_flag=gate_archived,
        required_variation=gate_archived.fallthrough_variation,
    )


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

# Contexts chosen to exercise each operator's match and non-match branch, every
# segment precedence rule, and every fail-closed case.
SCENARIO_CONTEXTS = [
    {"user_id": "alice", "plan": "pro", "country": "EG", "age": 30, "email": "alice@example.com"},
    {"user_id": "bob", "plan": "free", "country": "FR", "age": 70, "email": "bob@other.org"},
    # Excluded from `beta` but matched by its rule — exclusion must win.
    {"user_id": "carol", "plan": "pro", "country": "US", "age": 40},
    {"user_id": "dave", "plan": "pro"},
    # No attributes at all: a missing attribute never matches, whatever the operator.
    {"user_id": "eve"},
    # Fail-closed: `gt`/`lt` against an operand that will not coerce. Not an
    # error and not a match, on either operator.
    {"user_id": "frank", "age": "unknown", "plan": "pro"},
    {"user_id": "grace", "age": None, "plan": "pro"},
    # Boundary ages for gt 18 / lt 65 — strict comparison, so neither matches.
    {"user_id": "heidi", "age": 18, "plan": "pro"},
    {"user_id": "ivan", "age": 65, "plan": "pro"},
    # Numeric string, which must coerce the same as a number.
    {"user_id": "judy", "age": "19", "plan": "pro"},
    # No user_id at all: bucketing hashes the empty string rather than failing.
    {"plan": "pro", "country": "EG"},
]

# The flags whose answers the dense bucketing cases pin. Restricted on purpose:
# a full expectation map per sample would multiply the file size by the flag
# count for no extra coverage of the thing being probed.
BUCKETING_FLAGS = ("rollout-50", "rollout-0", "rollout-100", "rule-rollout-50")


def generate(environment: Environment) -> dict:
    """Run the engine over every case and return the vectors."""
    service = FlagEvaluationService()
    project_id = environment.project_id

    config = service.config_for(
        project_id=project_id,
        env_id=environment.id,
        environment_name=environment.name,
        config_version=environment.config_version,
    )

    cases = [
        _case(service, project_id, environment.id, context)
        for context in SCENARIO_CONTEXTS
    ]
    cases += [
        _case(
            service, project_id, environment.id,
            {"user_id": f"bucket-{i}", "plan": "pro"},
            only=BUCKETING_FLAGS,
        )
        for i in range(BUCKETING_SAMPLES)
    ]

    return {
        "format_version": CONFIG_FORMAT_VERSION,
        "config": config,
        "cases": cases,
    }


def _case(service, project_id: int, env_id: int, context: dict, only=None) -> dict:
    """One case: a user context and the engine's own answer for it.

    `expect` is a *partial* map — an SDK must reproduce the flags listed, and
    the absence of a flag says nothing about it.
    """
    evaluations = service.evaluate_all(
        project_id=project_id, env_id=env_id, user_context=context
    )
    return {
        "user_context": context,
        "expect": {
            evaluation.flag_key: {
                "result": evaluation.result,
                # Compared by id, never by value: two variations of one flag may
                # carry the same value, and prerequisites resolve on identity.
                "variation_id": evaluation.variation_id,
            }
            for evaluation in evaluations
            if only is None or evaluation.flag_key in only
        },
    }
