# 0001 — The project is the tenancy boundary

**Status:** Accepted

## Context

Flags belong to teams, not to individuals. If ownership were scoped by a user foreign key,
people couldn't share flags with different permissions, and flag keys would have to be
globally unique.

## Decision

Tenancy is `Organization → Membership(user, role) → Project`. Every queryset is scoped by
project membership (`organization__memberships__user`), not by an owner FK. Roles are
`viewer < member < admin < owner`.

## Consequences

- Flag keys are unique per project, so two teams can each own a `dark-mode` flag.
- Several people share the same flags with different roles.
- Every query and mutation must go through membership scoping. See
  [tenancy rules](../../.claude/rules/tenancy.md).
