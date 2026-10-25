---
name: engine-change
description: For any change to evaluation behaviour (precedence, operators, bucketing, segments, prerequisites, payload shape). Updates the conformance fixture, regenerates and explains the vectors, updates SDK_CONFIG_SPEC.md, and checks the query-count tests. Only when the user delegates the change.
argument-hint: "<the behaviour change>"
disable-model-invocation: true
---

# Change evaluation behaviour

Change: $ARGUMENTS

Every server-side SDK reimplements this engine from `GET /sdk/flags/config/`, so a
behaviour change is an SDK contract change. Read `.claude/rules/evaluation.md`,
`operators.md` and `cache.md`, and the relevant sections of `SDK_CONFIG_SPEC.md`, first.

1. **Snapshot.** Run `docker compose run --rm web python manage.py generate_conformance_vectors --check`.
   It must pass before you start, or the diff will mix your change with drift.
2. **Change the engine**, test first, under the matching `apps/*/tests/`. Keep fail
   closed: decide what an unresolvable input means before writing the code. Keep the
   flag-level bucketing hash exactly `f"{flag_key}{user_id}"`. A new payload field goes in
   `_build_flag_data` only.
3. **Update the fixture** in `apps/evaluation/conformance.py`, with a case that exercises
   the change and one that shows its boundary. Use explicit pks in the reserved bands,
   never sequence ids. A new operator also needs a matching and a non-matching context
   (`test_every_operator_is_exercised`).
4. **Regenerate** with `docker compose run --rm web python manage.py generate_conformance_vectors`.
   Never hand-edit `sdk_conformance_vectors.json`.
5. **Explain the diff in plain words.** Load the old file from `git show HEAD:sdk_conformance_vectors.json`,
   compare it with the new one case by case, and count the changed cases (each case maps
   flag keys to `{result, variation_id}`). Describe the changes like: "12 cases changed.
   Users whose `plan` attribute is missing no longer match `plan neq free`, so they now
   get `off_variation`." Every changed case must be one you expected. An unexpected change
   is a bug, so stop and report it.
6. **Update `SDK_CONFIG_SPEC.md`.** Precedence (§4.1), bucketing (§4.2), operators
   (§4.3), segments (§4.4) and fail closed (§4.5) are normative. If the wire format
   changed, decide with the user whether `format_version` moves.
7. **Check query counts.** Run the `django_assert_num_queries` tests listed in
   `.claude/rules/testing.md`. If a count moved, explain why; don't just bump the number.
8. **Run** the apps you touched, then `apps/evaluation/` and `apps/sdk/`.
9. **Report.** Give the behaviour change, the vector diff summary, the spec sections
   updated, the query counts, and the test results. If the reasoning behind a decision
   changed, propose a new ADR in `docs/adr/`. Don't commit.
