---
name: ship
description: Pre-merge gate for the current branch. Full test suite, ruff, both --check commands, /review-diff and /docs-sync, then a proposed commit message and PR text. Commits only after the user confirms, then pushes the branch (never main) and opens the PR. The user reviews and merges.
argument-hint: "[issue #]"
disable-model-invocation: true
---

# Ship the branch

Issue: $ARGUMENTS

Stop at the first step that fails, and report it. Don't fix anything inside this skill.

1. **Full suite**: `docker compose run --rm -T web pytest -q` (about a minute).
2. **Ruff** on the `.py` files changed vs `git merge-base main HEAD`:
   `uvx ruff@0.14.0 check <files>`. Separate new findings from pre-existing ones.
3. **Checks:**
   - `docker compose run --rm -T web python manage.py makemigrations --check --dry-run`
   - `docker compose run --rm -T web python manage.py generate_conformance_vectors --check`
4. **Review:** run the `/review-diff` skill. Critical or High findings block shipping
   unless the user explicitly accepts them.
5. **Docs:** run the `/docs-sync` skill, Phase 1 only. Note any drift the branch
   introduced.
6. **Propose**, without running anything:
   - A commit message in the repo's style (see `git log --oneline -15`): a
     `type: summary` line, a blank line, and short bullets. **No `Co-Authored-By` or other
     attribution trailer.**
   - A PR title and a body with: Summary (what and why), Changes (bullets), Testing (the
     commands above and their results), Notes for the reviewer (vector or query-count
     changes, migrations, any deploy ordering such as restarting the Celery worker), and
     `Closes #<n>` when an issue was given. **No "Generated with Claude Code" line or other
     attribution**, the same as commits.
   - The exact commands you'll run: `git add <files>` (list them, and flag any file
     that should be left out), `git commit -F <file>`, `git push -u origin <branch>` and
     `gh pr create --base main --head <branch> --title "<title>" --body-file <file>`.
7. **Wait for confirmation.** Commit only after the user confirms in this conversation.
   Write the message to a file under `.claude/plans/` (gitignored) with the Write tool
   and pass it with `-F`. Inline text that names a dotenv file trips the guard in
   `protect.sh`.
8. **Push the branch.** Check `git branch --show-current` first. If it is `main`, stop:
   never push main. Push with the branch named explicitly, `git push -u origin <branch>`.
   Never use a bare `git push`, `HEAD`, a `+refspec` or `--force`; settings deny them.
9. **Open the PR.** Write the PR body to `.claude/plans/pr-<branch>.md` (replace `/` in
   the branch name with `-`) with the Write tool, and end it with `Closes #<n>` when an
   issue was given. Run
   `gh pr create --base main --head <branch> --title "<title>" --body-file <that file>`.
10. **Stop.** Print the PR URL. **Never merge or close the PR**; the user reviews and
    merges.
