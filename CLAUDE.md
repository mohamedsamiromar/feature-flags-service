@AGENTS.md

## Workflow

The user writes most of the code. Claude plans, reviews as a second pair of eyes, and
implements only when explicitly delegated. Don't edit application code unless the user
asks for it in this conversation or invokes an implementing skill (`/implement`,
`/fix-bug`, `/new-endpoint`, `/engine-change`).

| Skill | Use it when |
| --- | --- |
| `/plan-task <issue # or description>` | Starting a task. Read-only: writes `.claude/plans/<slug>.md`, then runs `plan-reviewer`. |
| `/implement` | The user delegates an approved plan. Test first; run that app's tests after each step. |
| `/fix-bug` | Fixing a bug: write a failing test that reproduces it, show that it fails, then fix. |
| `/new-endpoint` | Adding API surface: query → service → serializer → view → URL → HTTP test → Postman. |
| `/engine-change` | Any change to evaluation behaviour: fixture, vectors, SDK_CONFIG_SPEC, query counts. |
| `/review-diff` | The user's main review loop: `code-reviewer` + `tenancy-reviewer` + the checks. Never edits. |
| `/docs-sync` | Checking README, PROJECT_GUIDE, AGENTS.md and the rules against the code. Fixes only after approval. |
| `/ship` | User-invoked only. Full suite, ruff, both `--check`s, review, docs, then a proposed commit and PR. |

Never push, commit or open a PR without the user's confirmation in the current
conversation.
