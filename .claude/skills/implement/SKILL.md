---
name: implement
description: Implement a plan from .claude/plans/ that the user has approved, step by step and test first. Only when the user delegates the implementation.
argument-hint: "<plan path or slug>"
disable-model-invocation: true
---

# Implement an approved plan

Plan: $ARGUMENTS

The user invoking this skill is the delegation. Implement **only** what the plan says.

1. **Load the plan.** Open `.claude/plans/<slug>.md`. If no plan was named, or several
   match, list them and ask which one. If the plan still has open questions, stop and ask
   them first.
2. **Load the rules.** Read `AGENTS.md`, and the `.claude/rules/*.md` files whose `paths`
   cover the plan's files.
3. **For each step, in order:**
   1. Write the step's test first. Run it, and confirm it fails for the reason the step
      addresses, not because of a typo or import error:
      `docker compose run --rm web pytest <test path> -q`.
   2. Write the smallest code that makes it pass, following the four layers.
   3. Run that app's tests: `docker compose run --rm web pytest apps/<app>/ -q`.
   4. Report in two or three lines: what changed, and the test result.
4. **Deviations.** If a step turns out wrong or a test reveals something the plan missed,
   stop and tell the user before changing course. Don't edit the plan silently.
5. **Finish.** Run `makemigrations --check` and, if the engine was touched,
   `generate_conformance_vectors --check`. Summarise the files changed, with the test
   results, and suggest `/review-diff`. Don't commit.
