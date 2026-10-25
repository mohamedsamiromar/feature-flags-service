---
paths:
  - "apps/*/queries.py"
  - "apps/*/services.py"
  - "apps/*/serializers.py"
  - "apps/organizations/**"
---

# Tenancy and access

The tenancy model is `Organization → Membership(user, role) → Project → {FeatureFlag,
Segment, Environment}`. The **project** is the boundary, not the user. Roles go
`viewer < member < admin < owner`. Why: ADRs [0001](../../docs/adr/0001-project-is-the-tenancy-boundary.md)
and [0002](../../docs/adr/0002-non-members-get-404.md).

## Membership scoping

Every queryset is scoped by project membership (`organization__memberships__user`), and
every mutation asserts the role:

```python
project = ProjectQuery.get_for_member(project_key, user)   # 404 if not a member
AccessService.assert_can_write(user, project)              # 403 if role too low
```

- A project you are not a member of returns **404**, which makes it invisible. A member
  with too low a role gets **403**.
- Never read across projects, even in admin or debug paths.

## Foreign-key ownership

Every writable foreign key is ownership-checked in the service, on create **and** update.

- A `ModelSerializer` resolves a pk against the whole table, across every tenant, and the
  engine serves a referenced variation's value verbatim. `off_variation`,
  `fallthrough_variation` and `serve_variation` once let any account read another tenant's
  values through its own SDK key.
- The engine enforces it too: `_variation_dict` drops a variation whose `flag_id` is not
  the flag's, because rows written before the write-path check still exist. Do not remove
  it as redundant. `manage.py clear_foreign_variation_refs [--apply]` finds and clears
  those rows.
- A child's parent (e.g. `Rule.flag`) is immutable after creation. A move would be
  authorized against the destination only, so it would let a viewer strip targeting from a
  flag they cannot edit.

## Owner rank

Only an owner may grant `owner`, or change or remove an owner's membership
(`MembershipService._assert_may_touch_owner_rank`). ADMIN manages members, not the rank
above it. An admin who could promote themselves would lift the last-owner guard, then
demote the real owner and delete the org.

## Invitations

Nobody joins an organization without consent. A `Membership` is created only when the
invitee accepts an `Invitation`; there is no direct add. The inviter's authority is
re-checked at accept time, so an invitation from someone who has since left, or lost the
rank it grants, is void (409 `INVITATION_VOID`).

## Signup provisions tenancy, not just a user

`POST /api/v1/auth/register/` creates the personal organization, the `Default` project and
three environments alongside the user, in one transaction. That is the same shape
`organizations/0002_backfill_personal_orgs` gave existing users. Any *required* new piece
of per-account setup belongs in `RegistrationService.register`, inside that transaction:
an account missing one of these pieces has no API path back to a working state.
