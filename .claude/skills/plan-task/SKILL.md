---
name: plan-task
description: Plan a task without writing code. Given an issue number, asks the user to paste the issue text, explores the code, writes .claude/plans/<slug>.md, then runs plan-reviewer and shows both. Use when the user starts a task or asks for a plan.
argument-hint: "<issue # or description>"
allowed-tools: Bash(git log *), Bash(git diff *)
---

# Plan a task (read-only)

Task: $ARGUMENTS

**Write no code.** The only file you may create or change is the plan itself.

1. **Understand the task.** If the argument is an issue number (`42` or `#42`), ask the
   user to paste the issue text and comments, and wait for them. Don't fetch it: Claude
   has no GitHub access. Otherwise use the description as given. If the goal is
   ambiguous, list your questions in the plan; don't guess.
2. **Explore.** Read `AGENTS.md`, find the code involved (Grep/Glob), and read it. Read
   every `.claude/rules/*.md` file whose `paths` cover a file you'll touch, and any ADR
   in `docs/adr/` the change bears on. Check `git log` for how similar changes were made.
3. **Write `.claude/plans/<slug>.md`.** Use a short kebab-case slug, prefixed with the
   issue number if there is one (`42-stale-flag-detection`). Use this outline:

   ```markdown
   # <title>
   Issue: #<n> or none · Status: draft

   ## Goal
   One paragraph: what changes for whom, and what is explicitly out of scope.

   ## Files
   Each file to add or change, with the layer (query / service / serializer / view / URL /
   test / migration / docs) and one line on the change.

   ## Steps
   Numbered, in implementation order. Each step is small enough to test on its own and
   names the test that proves it.

   ## Tests
   New tests, through which layer, including the failure cases: non-member 404, low-role
   403, cross-tenant FK, invalid input. For each safety property, say how you'll break it
   to show the test can fail.

   ## Invariants and rules involved
   The key invariants (by number) and the rule files and ADRs this touches, with how the
   plan respects each.

   ## Vectors and query counts
   Does evaluation behaviour change (fixture, vectors, SDK_CONFIG_SPEC)? Which
   `django_assert_num_queries` tests could move, and by how much? "None", with the reason,
   is a valid answer.

   ## Open questions
   Decisions the user must make before implementing.
   ```

4. **Review.** Run the `plan-reviewer` subagent on the plan's path.
5. **Show both.** Give the user the plan path, a short summary of the plan, and the
   reviewer's findings by severity. Don't revise the plan from the review on your own.
   Propose the changes and let the user decide.
