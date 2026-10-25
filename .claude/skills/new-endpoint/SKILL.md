---
name: new-endpoint
description: Checklist for adding API surface. Covers a new resource or an @action on an existing ViewSet, in order from query → service → serializer → view → URL → HTTP test → Postman collection. Only when the user delegates the endpoint.
argument-hint: "<what the endpoint does>"
disable-model-invocation: true
---

# Add an endpoint

Endpoint: $ARGUMENTS

Read `AGENTS.md`, `.claude/rules/tenancy.md`, `audit.md`, `cache.md` and `testing.md`
first, plus `sdk.md` for anything under `/sdk/`. Work through the checklist **in order**,
and tick each item off in your report.

**Decide the shape first.** Is this a new resource (its own ViewSet and URL include) or
an action on an existing one? For example, a new flag operation is an `@action` on
`FlagViewSet` in `apps/flags/views.py`, as `archive`, `toggle` and `versions` are. Copy
the nearest existing example of the same shape.

1. **Query** (`apps/<app>/queries.py`). Add a `*Query` method for every read and write.
   Every read is scoped by membership. This is the only place with ORM access.
2. **Service** (`apps/<app>/services.py`). Add or extend a `*Service` method.
   - Fetch through `ProjectQuery.get_for_member` (404), then
     `AccessService.assert_can_write` (403) for mutations.
   - Check every writable FK belongs to the same flag or project, on create **and** update.
   - An `update_*` method takes `<entity>_key`, never `key`.
   - Archived flag → `FlagService._assert_active`.
   - Raise `APIError(Error.X)`. For a new error, add it to `apps/core/errors.py` with the
     next unused code.
   - Audit: `AuditService.snapshot` first, then `log` (or `log_delete` for a delete), for
     successful mutations only.
   - If what a flag serves changes, call `FlagService.invalidate_flag_caches` after the
     write commits, never inside `atomic()`.
3. **Serializer.** Field shape only. No DB access and no cross-entity checks.
4. **View.** Request/response only. For an `@action`, set `detail` and `methods`, plus
   `url_path` when the name differs. Splat `**serializer.validated_data` next to the
   lookup argument.
   - A route keyed by a user identifier needs an explicit `[^/]+`, as in
     `url_path=r"targets/(?P<user_key>[^/]+)"`. DRF's default detail regex is `[^/.]+`,
     which stops at the first dot and silently truncates email-shaped keys. Test it with
     a key that contains a dot.
5. **URL.** Register on the app's router, or rely on the `@action`. Confirm the full path
   resolves: `docker compose run --rm web python manage.py shell -c "from django.urls import resolve; print(resolve('/api/v1/...'))"`.
6. **Tests through HTTP** (`apps/<app>/tests/`). Cover the happy path; a non-member (404,
   with the same body as a missing resource); a member below the required role (403); a
   cross-tenant FK (rejected); invalid input (400 with the expected `code`); the audit row
   written, or not written on rejection; and cache invalidation, if the endpoint changes
   evaluation. Break one guard and confirm its test fails. Run
   `docker compose run --rm web pytest apps/<app>/ -q`.
7. **Postman collection** (`feature_flags.postman_collection.json`). Add the request to
   the matching folder, using `{{base_url}}`, `{{project_key}}` and `{{access_token}}` (or
   `{{sdk_key}}`). The file is hand-formatted, so don't `json.dump(indent=2)` it. Re-emit
   it with a width-aware encoder (compact any node whose one-line `json.dumps` fits ~100
   chars, indent 2 otherwise). If `postman_format.py` exists next to this SKILL.md, use it.
   Otherwise write it, prove it round-trips the current file with a zero-line diff, and
   save it there. Then check every request URL resolves with
   `django.urls.resolve()`, and that its method is allowed.
8. **Report.** List the files changed and the test results. Note which docs now drift
   (README API reference, PROJECT_GUIDE §5 and §9) for `/docs-sync`. Don't commit.
