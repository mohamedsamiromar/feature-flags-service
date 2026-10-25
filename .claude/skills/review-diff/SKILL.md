---
name: review-diff
description: Review the current branch's diff against main, committed and uncommitted, by running code-reviewer and tenancy-reviewer in parallel plus the repo checks. If the user wrote notes in .claude/reviews/<branch>.md, compares the findings with them. Never edits. Use when the user asks for a review of their changes.
allowed-tools: Bash(git rev-parse *), Bash(git merge-base *), Bash(git ls-files *), Bash(git diff *), Bash(git log *), Bash(git status *), Bash(uvx ruff@0.14.0 check *)
---

# Review the diff

**Never edit, create or delete any file during this skill.** The output is a report.

1. **Scope.** Find the branch (`git rev-parse --abbrev-ref HEAD`). The base is always
   `--merge-base main`, never `main` itself, so commits that landed on main after the
   branch started don't show up as reversed changes. Collect:
   - the changed tracked files: `git diff --name-only --merge-base main`, which covers
     committed, staged and unstaged changes;
   - the untracked files: `git ls-files --others --exclude-standard`.

   If both are empty, say so and stop.
2. **Launch both reviewers in parallel**, in one message with two Agent calls:
   `code-reviewer` and `tenancy-reviewer`. Tell each to diff with
   `git diff --merge-base main`, and give it the list of untracked files, since `git diff`
   can't show them. Skip `tenancy-reviewer` only if no
   changed file is Python.
3. **Run the checks** while they work, in the same message:
   - `docker compose run --rm -T web python manage.py makemigrations --check --dry-run`
   - `docker compose run --rm -T web python manage.py generate_conformance_vectors --check`,
     if anything under `apps/evaluation|targeting|segments|rules|flags` changed
   - `docker compose run --rm -T web pytest -q <the touched apps/<app>/ dirs>`
   - `uvx ruff@0.14.0 check <changed .py files>`. Separate findings on lines the diff
     touched from pre-existing ones (`git diff --merge-base main -- <file>` shows which lines changed).

   If Docker isn't running, say which checks didn't run. Don't start the stack.
4. **Merge the findings.** De-duplicate what both reviewers flagged, keep the most severe
   rating, and verify each Critical/High finding yourself by reading the cited lines. Drop
   any you can't substantiate, and say that you dropped them. Group by severity
   (Critical, High, Medium, Low), with `file:line` and a one-line failure scenario, then
   list the check results.
5. **Compare with the user's notes**, if `.claude/reviews/<branch>.md` exists, with `/` in
   the branch name replaced by `-` (`chore/claude-workflow` → `chore-claude-workflow.md`).
   Add three sections:
   - **We both caught**: findings that match a note.
   - **Only you caught**: notes with no matching finding. Say whether a reviewer missed
     it, or whether you think the note is mistaken, and why.
   - **You missed**: findings no note covers, most severe first.

   Be specific and neutral: this is a calibration exercise, not a score.
6. **End** with the top three things to fix before merging, or "nothing blocking".
