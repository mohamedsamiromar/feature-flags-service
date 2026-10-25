# 0006 — Rule-level bucketing is salted with the rule id; flag-level is not

**Status:** Accepted

## Context

Without a salt, two rules at the same percentage hash identically and select exactly the
same users, so a second 20% rollout reaches the same 20% of people.

## Decision

Rule-level rollout is salted with the rule id. Flag-level rollout keeps hashing exactly
`f"{flag_key}{user_id}"`, with no salt.

## Consequences

- Two rules at the same percentage pick different slices.
- The flag-level hash decides who already has a flag. Changing its inputs would re-bucket
  every user and flip live flags on deploy.
- Because rule ids feed the hash, the conformance fixture needs explicit pks (see
  [0018](0018-server-sdks-download-the-ruleset.md)).
