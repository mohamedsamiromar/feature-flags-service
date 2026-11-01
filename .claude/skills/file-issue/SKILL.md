---
name: file-issue
description: Open a GitHub issue from a draft in .claude/plans/issues/ or from a description. Shows the title, labels and body, and runs gh issue create only after the user confirms. User-invoked only.
argument-hint: "<draft file or number, or a description>"
disable-model-invocation: true
allowed-tools: Bash(gh issue list *), Bash(gh issue view *), Bash(gh label list *)
---

# File an issue

Issue: $ARGUMENTS

Nothing is created until the user confirms in this conversation. Stop at the first step
that fails, and report it.

This skill relies on `.claude/plans/issues/`, which is gitignored, so the drafts exist
only on this machine.

1. **Build the draft.**
   - **A draft is named** (a path, a file name, or a draft number such as `03`): find it in
     `.claude/plans/issues/`. Line 1 is the title, and line 2 is `Labels: a, b`. The body is
     everything after the blank line that follows them. Don't reword it.
   - **A description is given:** write the title and body in the drafts' style. The title
     is `type: summary`. The body has **Problem.**, **Evidence.** (with `file:line`
     references you have read), **Proposed fix.** and **Acceptance.** Suggest labels.
   - **Cross-references:** draft bodies cite *draft* numbers (`#03`, `#13`), not GitHub
     issue numbers. List every `#N` in the body, and ask the user what each one should
     become before filing: the real issue number, a plain-text title, or removed.
2. **Check for a duplicate.** Search open and closed issues for the title's key words:
   `gh issue list --state all --search "<words>" --limit 10`. Show any likely match.
3. **Check the labels.** Run `gh label list --limit 200`. For each label the issue needs
   that doesn't exist yet, ask the user whether to create it, then run
   `gh label create <name> --description "<one line>"`. Never create a label without
   asking, and never drop a label silently.
4. **Show it.** Show the user the exact title, labels and body, then wait for
   confirmation. Then write the body to `.claude/plans/issue-<slug>.md` with the Write
   tool. The file holds the final text the user confirmed, including any edits they asked
   for.
   The body goes through a file because text that names a dotenv file trips the guard in
   `protect.sh`.
5. **Create it.** After the user confirms, run
   `gh issue create --title '<title>' --label '<a>' --label '<b>' --body-file <that file>`.
   The title is single-quoted, so backticks and `$(` stay literal. If it contains a `'`,
   reword it.
   If the title itself names a dotenv file, reword the title and say so, since the title
   can't be passed by file.
6. **Report.** Give the issue number and URL. When it came from a draft, tick that draft's
   line in `.claude/plans/issues/README.md` and add the number, e.g.
   `- [x] \`03-…\` (security, bug) — #31`.

Never close, delete or edit an issue in this skill. Editing an existing issue is a
separate request, and `gh issue edit` asks for permission.
