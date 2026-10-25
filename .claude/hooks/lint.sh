#!/usr/bin/env bash
# PostToolUse lint for Edit|Write: ruff on the edited .py file only, never the
# whole repo. Findings go back to Claude (exit 2 shows stderr to Claude; the
# edit itself has already happened). Fails open if a tool is missing.

# Keep in step with requirements-dev.txt.
RUFF_VERSION=0.14.0

if ! command -v python3 >/dev/null 2>&1; then
  echo '{"systemMessage": "lint.sh: python3 not found, lint skipped (fail open)"}'
  exit 0
fi

file=$(python3 -c '
import json, sys
try:
    print((json.load(sys.stdin).get("tool_input") or {}).get("file_path", ""))
except ValueError:
    pass
')

root=${CLAUDE_PROJECT_DIR:-$PWD}
case "$file" in
  "$root"/venv/*|*/migrations/*) exit 0 ;;   # third-party and generated code
  *.py) ;;
  *) exit 0 ;;
esac
[ -f "$file" ] || exit 0

if ! command -v uvx >/dev/null 2>&1; then
  echo '{"systemMessage": "lint.sh: uvx not found, ruff skipped (fail open)"}'
  exit 0
fi

cd "$root" || exit 0
if ! out=$(uvx --quiet "ruff@$RUFF_VERSION" check --no-cache --output-format concise "$file" 2>&1); then
  # Exit 1 means findings. Anything else means ruff itself failed to run.
  if printf '%s' "$out" | grep -q 'Found [0-9]* error'; then
    printf 'ruff found problems in %s:\n%s\n' "${file#"$root"/}" "$out" >&2
    exit 2
  fi
  python3 -c 'import json,sys; print(json.dumps({"systemMessage": "lint.sh: ruff did not run (fail open): " + sys.argv[1][:300]}))' "$out"
fi
exit 0
