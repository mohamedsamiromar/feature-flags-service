# 0002 — "Not yours" returns 404, not 403

**Status:** Accepted

## Context

A `403` confirms that a resource exists. In a multi-tenant API, that leaks the existence
of other teams' projects and flag keys.

## Decision

A project you are not a member of, and everything inside it, returns **404**: it is
invisible. A member whose role is too low gets **403**.

## Consequences

- Lookups resolve through `ProjectQuery.get_for_member` (which 404s) before
  `AccessService` asserts the role (which 403s). The order matters.
- The same principle later applied to client SDK keys: a hidden flag is a 404
  indistinguishable from a missing key.
