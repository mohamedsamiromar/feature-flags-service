#!/usr/bin/env bash
# Stop hook: before Claude finishes, check the Python files Claude itself
# edited in this session. track-edits.sh records them in
# .git/claude-edits/<session_id>. The user's own uncommitted work is not
# checked here; reviewing it is /review-diff's job.
#
#   1. makemigrations --check
#   2. generate_conformance_vectors --check, if an edited file is under
#      apps/evaluation, targeting, segments, rules or flags
#   3. pytest for the apps of the edited files only (the full suite is /ship's)
#
# Everything runs through `docker compose run --rm -T web`, so this script
# never reads .env. A failure blocks the stop with the failing output. A pass
# clears the tracking file, so the next stop checks only new edits.
#
# Loop guard: when stop_hook_active is true, Claude is already continuing
# because of this hook, so it never blocks twice in a row. Fails open if
# git, python3 or Docker is unavailable.

say() {  # a message for the user, without blocking
  python3 -c 'import json,sys; print(json.dumps({"systemMessage": "stop-check: " + sys.argv[1]}))' "$1"
}

command -v python3 >/dev/null 2>&1 || { echo '{"systemMessage": "stop-check: python3 not found, checks skipped (fail open)"}'; exit 0; }

read -r active session < <(python3 -c '
import json, re, sys
try:
    event = json.load(sys.stdin)
except ValueError:
    event = {}
session = re.sub(r"[^A-Za-z0-9_-]", "", str(event.get("session_id", "")))
print(str(bool(event.get("stop_hook_active"))).lower(), session or "-")
')
[ "$active" = "true" ] && exit 0
[ "$session" = "-" ] && exit 0

root=${CLAUDE_PROJECT_DIR:-$PWD}
cd "$root" || exit 0
command -v git >/dev/null 2>&1 || { say "git not found, checks skipped (fail open)"; exit 0; }
track_file="$(git rev-parse --absolute-git-dir 2>/dev/null)/claude-edits/$session"
[ -f "$track_file" ] || exit 0

edited=$(sort -u "$track_file" | grep '\.py$' | grep -v '^venv/')
[ -z "$edited" ] && exit 0

if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
  say "Docker is not running, your edits were not checked (fail open)"
  exit 0
fi

run() { docker compose run --rm -T web "$@" 2>&1; }
failures=""

out=$(run python manage.py makemigrations --check --dry-run) || failures+=$'\n## makemigrations --check\n'"$(printf '%s' "$out" | tail -40)"

if printf '%s\n' "$edited" | grep -qE '^apps/(evaluation|targeting|segments|rules|flags)/'; then
  out=$(run python manage.py generate_conformance_vectors --check) || failures+=$'\n## generate_conformance_vectors --check\n'"$(printf '%s' "$out" | tail -40)"
fi

apps=$(printf '%s\n' "$edited" | sed -n 's|^\(apps/[^/]*\)/.*|\1/|p' | sort -u)
if [ -n "$apps" ]; then
  # shellcheck disable=SC2086  # one argument per app directory
  out=$(run pytest -q -p no:cacheprovider $apps)
  status=$?
  # 5 = no tests collected, e.g. an app without tests: not a failure.
  if [ $status -ne 0 ] && [ $status -ne 5 ]; then
    failures+=$'\n## pytest '"$(echo $apps)"$'\n'"$(printf '%s' "$out" | grep -vE '^ Container|^ Network' | tail -80)"
  fi
fi

if [ -n "$failures" ]; then
  python3 -c 'import json,sys; print(json.dumps({"decision": "block", "reason": "Checks failed after your edits. Fix problems in files you edited; if a failure comes from a file you didn'"'"'t edit, report it to the user and don'"'"'t touch it.\n" + sys.argv[1]}))' "$failures"
  exit 0
fi

rm -f "$track_file"
exit 0
