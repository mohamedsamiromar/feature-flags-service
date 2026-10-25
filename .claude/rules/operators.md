---
paths:
  - "apps/targeting/**"
  - "apps/rules/**"
  - "apps/segments/models.py"
  - "apps/segments/services.py"
  - "apps/evaluation/conformance.py"
---

# Targeting operators

`Operator` in `apps/rules/models.py` defines nine operators: `eq`, `neq`, `contains`, `in`,
`not_in`, `gt`, `lt`, `in_segment`, `not_in_segment`. `RuleEvaluator` in
`apps/targeting/services.py` matches them.

- The segment operators (`Operator.segment_operators()`) ignore `attribute`. `value` holds
  a segment key, and `SegmentEvaluator` resolves the membership.
- `SegmentRule` forbids the segment operators, because segments do not nest (see
  `apps/segments/models.py`).
- An unknown segment never matches, whatever the operator.

## Numeric operators coerce, and fail closed when they cannot

`gt` / `lt` (`Operator.numeric_operators()`) run through `RuleEvaluator._evaluate_numeric`.
It coerces both sides with `rules.models.to_number` and returns False if either side will
not convert. `to_number` is the single coercion rule: evaluation and write-time validation
share it, so they can never disagree about what counts as a number.

Until 2026-08-29 this was a bare `float(user_value)`. A rule like `age gt 18` against
`{"age": "unknown"}` raised `ValueError`, uncaught, and returned a **500 on
`POST /sdk/evaluate/`**. The user context is arbitrary runtime input from the caller's own
application, so it can never be validated ahead of time. The only options were to fail
closed or to return an error from the customer's hot path, and failing closed matches
every other unresolvable case in the engine.

**Neither operator may be written as the negation of the other.** Both return False on
junk, so `not gt` would match every user whose attribute is unusable.

## Write-time check

Authored rule `value`s are checked at write time. `RuleService._assert_numeric_value` and
`SegmentService._assert_numeric_value` reject a non-numeric value for `gt`/`lt` with
`NON_NUMERIC_COMPARISON` (-418), because a rule that fails closed forever with nothing to
show why is a silent misconfiguration. The check reads the rule as it will be *after* the
write, so changing only the operator cannot slip past it. Any change to the check must go
into **both** services.

## Adding an operator

1. Add the constant to `Operator`.
2. Implement the match branch in `RuleEvaluator`. **Decide what a failed coercion means
   before you write it.** The engine's answer everywhere else is "does not match", and the
   new operator must never be expressible as the negation of another.
3. If it needs write-time validation (as `gt`/`lt` do), add it to both `RuleService` and
   `SegmentService`.
4. Add it to the conformance fixture in `apps/evaluation/conformance.py`, with one user
   context that matches and one that does not. `test_every_operator_is_exercised` fails if
   you skip this step.
5. Regenerate the vectors and review the diff (see [evaluation.md](evaluation.md)).
6. Add match and non-match tests under `apps/targeting/tests/`.
