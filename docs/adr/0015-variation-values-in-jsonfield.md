# 0015 — Variation values are stored in a JSONField

**Status:** Accepted

## Context

A variation must be able to hold a boolean, a string, a number or an arbitrary JSON object.

## Decision

`Variation.value` is a `JSONField`, and `value_type` records which kind is stored.

## Consequences

- All four kinds are covered without a column per type.
- Clients deserialise the value using `value_type`.
