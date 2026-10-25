---
name: fix-bug
description: Fix a bug test-first. Write a failing test that reproduces it, show that it fails, then fix it. Only when the user delegates a fix.
argument-hint: "<bug description or issue #>"
disable-model-invocation: true
allowed-tools: Bash(gh issue view *)
---

# Fix a bug, test first

Bug: $ARGUMENTS

1. **Understand.** If given an issue number, run `gh issue view <n> --comments`. Find the
   code path, and read the `.claude/rules/*.md` files whose `paths` cover it.
2. **Reproduce with a test.** Write a test that fails *because of the bug*. Put it in the
   app's `tests/` directory, and test through the layer where the bug is visible: an HTTP
   test (`auth_client` / `api_client`) if the user sees it through the API. Use the
   conftest factories and a real database; don't mock the ORM.
3. **Show that it fails.** Run only that test:
   `docker compose run --rm web pytest <path>::<test> -q`. Show the failing output to the
   user, and say in one line why this failure is the bug, not a setup error.
4. **Fix.** Make the smallest change that fixes the cause, not the symptom, and keep to
   the four layers. If the fix changes evaluation behaviour, follow `/engine-change`'s
   steps for the vectors.
5. **Prove it.** Re-run the test (it should pass), then the app's suite:
   `docker compose run --rm web pytest apps/<app>/ -q`.
6. **Report.** Give the cause, the fix, the test, and whether the same bug pattern
   exists elsewhere (Grep for it, but list other occurrences rather than fixing them).
   Don't commit.
