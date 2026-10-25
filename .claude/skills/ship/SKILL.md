---
name: ship
description: Pre-merge gate for the current branch. Full test suite, ruff, both --check commands, /review-diff and /docs-sync, then a proposed commit message and PR body. Commits and opens the PR only after the user confirms.
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
     `Closes #<n>` when an issue was given.
   - The exact commands you'll run: `git add <files>` (list them, and flag any file
     that should be left out), `git commit -F <file>`, `git push -u origin <branch>`, and
     `gh pr create --title "<title>" --body-file <file>`.
7. **Wait for confirmation.** Commit, push and open the PR only after the user confirms
   in this conversation. Write the message and body to files under `.claude/plans/`
   (gitignored) with the Write tool, and pass them with `-F` / `--body-file`. Inline text
   that names a dotenv file trips the guard in `protect.sh`.
