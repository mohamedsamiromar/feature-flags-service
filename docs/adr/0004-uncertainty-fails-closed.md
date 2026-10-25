# 0004 — Uncertainty fails closed

**Status:** Accepted

## Context

References can dangle: a segment key that no longer exists, an archived, missing or
cyclic prerequisite, or a user attribute a `gt`/`lt` rule cannot read as a number.

## Decision

Anything unresolvable matches nobody and leaves the flag off. **Never invert an
unresolvable reference.**

## Consequences

- A `not_in_segment` rule that "matched" on an unknown segment would turn one dangling
  reference into a full rollout, so unknown segments never match, whatever the operator.
- `lt` must not be written as `not gt`. Both return False on junk, and the negation would
  match every user whose attribute is unusable.
- Some races are benign because of this rule, for example a rule created concurrently
  with a segment delete. Do not "fix" them by inverting the fallback.
