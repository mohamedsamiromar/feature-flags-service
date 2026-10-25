# 0016 — Boolean flags are auto-wired on creation

**Status:** Accepted

## Context

Boolean flags should work with zero configuration, while the engine keeps one
variation-based code path.

## Decision

`FlagService.create_flag()` gives every boolean flag `true`/`false` variations and wires
them to `fallthrough_variation` and `off_variation`.

## Consequences

- There is one evaluation path for boolean and multivariate flags.
- A flag with no variations still returns raw `true`/`false` as a legacy fallback.
