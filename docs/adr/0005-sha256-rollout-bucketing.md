# 0005 — SHA-256 for rollout bucketing

**Status:** Accepted

## Context

A percentage rollout has to put the same user in the same bucket every time, and every
SDK has to be able to compute that bucket identically.

## Decision

Bucket with `SHA-256(flag_key + user_id) % 100 < rollout_percentage`.

## Consequences

- The assignment is deterministic per `(flag_key, user_id)`.
- SHA-256 is available in every standard library, so it is trivial to reimplement in any
  SDK language. LaunchDarkly uses MurmurHash3. SHA-256 is slower, but needs no dependency.
