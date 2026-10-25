# 0009 — Bulk download returns evaluated results, not raw config

**Status:** Superseded in part by [0018](0018-server-sdks-download-the-ruleset.md). This
still holds for the client bootstrap; server-side SDKs now download the ruleset.

## Context

Shipping the raw config would mean every SDK reimplements SHA-256 bucketing, rule
precedence, segment membership and prerequisite cycle detection. Any divergence between
an SDK and the server serves the wrong value to real users.

## Decision

`POST /sdk/flags/evaluate/` resolves every flag server-side, for one user context.

## Consequences

- There is one implementation of the engine.
- An SDK cannot evaluate offline, or re-evaluate a changed context, without another call.
  The cost is one round trip per user context, which is right for a browser (one user per
  session) and wrong for a server evaluating thousands of users. That cost is what led
  to 0018.
