# 0007 — Segments do not nest

**Status:** Accepted

## Context

If a segment could reference another segment, membership would need recursive resolution
and a cycle detector of its own.

## Decision

`SegmentRule` forbids the segment operators (`in_segment`, `not_in_segment`).

## Consequences

- Segment membership is resolved in a single pass.
- Dependencies between features are expressed with flag-level prerequisites instead.
