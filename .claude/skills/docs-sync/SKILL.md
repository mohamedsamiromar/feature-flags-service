---
name: docs-sync
description: Check README.md, PROJECT_GUIDE.md, SDK_CONFIG_SPEC.md, AGENTS.md, .claude/rules/, docs/adr/ and docs/recipes.md against the code (test count, endpoints, ports, versions, cache key, named symbols), and list the drift. Fixes only after the user approves. Use when the user asks whether the docs are current.
allowed-tools: Bash(git log *), Bash(git diff *)
---

# Check the docs against the code

The code is the source of truth. **Phase 1 changes nothing.** Phase 2 runs only after
the user approves specific fixes.

## Phase 1: list the drift

Measure each fact from the code, then find every place the docs state it:

| Fact | Measure it with |
| --- | --- |
| Test count | `docker compose run --rm -T web pytest --collect-only -q`, last line |
| Endpoints and methods | `config/urls.py`, each app's `urls.py`, the `@action`s in `views.py`. Compare with README → API reference and PROJECT_GUIDE §5 and §9 |
| Ports | `docker-compose.yml` (web, db, redis host ports) |
| Versions | `Dockerfile` (Python), `requirements.txt`, `docker-compose.yml` images, `.github/workflows/ci.yml`, `requirements-dev.txt` (ruff) |
| Cache key | `FlagEvaluationService._cache_key` and `_generation_key` in `apps/evaluation/services.py` |
| Error codes | the `Error` enum in `apps/core/errors.py`: are codes unique, and are numbers quoted in the docs correct? |
| Roadmap and status | README → Roadmap, PROJECT_GUIDE §6–§8, AGENTS.md → Overview: do they agree with each other? |
| Named symbols | every backticked identifier in `AGENTS.md`, `.claude/rules/*.md` and `docs/adr/*.md` that looks like code (`Name`, `name()`, `module.attr`). Grep the code for each one and list those that no longer exist |
| Rule paths | every `paths` glob in `.claude/rules/*.md` still matches at least one file |
| Links | relative Markdown links in these files resolve to existing files |

Report one table: **file:line · what the doc says · what the code says · proposed fix**.
Group it by file, and end with a count. If nothing drifted, say so.

## Phase 2: fix (only after approval)

Apply exactly the fixes the user approved. Keep the surrounding style. Don't touch
application code under `apps/` or `config/`; list code-side problems (like a duplicate
error code) as follow-ups instead. Show the diff when done. Don't commit.
