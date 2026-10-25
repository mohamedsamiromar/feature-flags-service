#!/usr/bin/env bash
# PreToolUse guard for Edit|Write and Bash.
#
# Edit|Write: blocks .env files (except .env.example), the generated
#   sdk_conformance_vectors.json, and migrations already committed on main.
# Bash: blocks commands that name a .env file (except .env.example). The
#   permission deny on Read/Edit does not cover a shell `grep` or a script.
#
# Exit 2 blocks the call, and stderr is the reason Claude sees. Anything this
# hook cannot evaluate fails open: it allows the call and says why.

if ! command -v python3 >/dev/null 2>&1; then
  echo '{"systemMessage": "protect.sh: python3 not found, guard skipped (fail open)"}'
  exit 0
fi

exec python3 -c '
import json, os, re, subprocess, sys

def fail_open(why):
    print(json.dumps({"systemMessage": f"protect.sh: {why}, guard skipped (fail open)"}))
    sys.exit(0)

def block(reason):
    print(reason, file=sys.stderr)
    sys.exit(2)

try:
    event = json.load(sys.stdin)
except ValueError:
    fail_open("unreadable hook input")

root = os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or os.getcwd()
tool = event.get("tool_name", "")
tool_input = event.get("tool_input") or {}

# A .env file name: .env or .env.<anything>, except .env.example.
ENV_NAME = re.compile(r"^\.env(\..+)?$")

def is_secret_env(name):
    return bool(ENV_NAME.match(name)) and name != ".env.example"

if tool == "Bash":
    command = tool_input.get("command", "")
    # Token-ish scan: any word ending in /.env or .env.<x>, quotes stripped.
    for token in re.split(r"[\s;|&()<>`=]+", command):
        name = os.path.basename(token.strip("\"\x27"))
        if is_secret_env(name):
            block(
                f"Blocked: this command names {name}, which holds secrets. "
                "Use .env.example for variable names; tests and checks run through "
                "`docker compose run --rm web ...`, which loads .env itself."
            )
    sys.exit(0)

if tool not in ("Edit", "Write"):
    sys.exit(0)

path = tool_input.get("file_path", "")
if not path:
    fail_open("no file_path in tool input")
rel = os.path.relpath(os.path.abspath(path), root)

if is_secret_env(os.path.basename(rel)):
    block(f"Blocked: {rel} holds secrets and is never edited by an agent. Change .env.example instead.")

if rel == "sdk_conformance_vectors.json":
    block("generated: run generate_conformance_vectors "
          "(docker compose run --rm web python manage.py generate_conformance_vectors) and review the diff.")

if re.match(r"^apps/[^/]+/migrations/[^/]+\.py$", rel) and not rel.endswith("/__init__.py"):
    try:
        committed = subprocess.run(
            ["git", "-C", root, "cat-file", "-e", f"main:{rel}"],
            capture_output=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        fail_open("git unavailable, cannot tell whether the migration is committed")
    if committed.returncode == 0:
        block(f"Blocked: {rel} is already committed on main, so it may be applied somewhere. "
              "Write a new migration instead (makemigrations).")
sys.exit(0)
'
