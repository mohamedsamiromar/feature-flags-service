# 0003 — Kill switch first, then prerequisites, then individual targets

**Status:** Accepted

## Context

The kill switch is what you reach for during an incident. Individual targets let you pin
a user to a variation. Prerequisites gate one feature on another.

## Decision

Evaluation order: kill switch → prerequisites → individual targets → rules → percentage
rollout. Each step short-circuits.

- **Nothing overrides the kill switch.** If any targeting layer could, turning a flag off
  would stop being a reliable way to stop it.
- **Prerequisites sit above individual targeting.** Otherwise pinning a user would bypass
  a dependency, and the flag could serve someone whose upstream feature is off.

## Consequences

- Individual targets do not override the kill switch, by design.
- The order is part of the SDK contract (SDK_CONFIG_SPEC §4.1) and is pinned by the
  conformance vectors.
