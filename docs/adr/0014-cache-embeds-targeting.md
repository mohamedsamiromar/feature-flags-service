# 0014 — The cached payload embeds targeting, so targeting writes invalidate it

**Status:** Accepted

## Context

The cached payload carries the flag's rules, targets and prerequisites, and the resolved
segments its rules reference. It holds everything evaluation needs, without another query.

## Decision

Every rule, target, prerequisite, variation and segment mutation invalidates the affected
flags' caches. A segment edit fans out to every flag that references the segment, through
one bulk query (`FlagService.invalidate_many_flag_caches`).

## Consequences

- A cache hit evaluates with zero DB queries.
- Any missed invalidation path causes incorrect evaluations, so the rule covers every
  write.
