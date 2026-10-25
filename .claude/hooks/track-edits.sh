#!/usr/bin/env bash
# PostToolUse on Edit|Write: record which files Claude edited in this session,
# so stop-check.sh checks Claude's own edits and not the user's uncommitted
# work in progress. Appends the repo-relative path to
# .git/claude-edits/<session_id>. Fails open: a missed record only means a
# check is skipped.

command -v python3 >/dev/null 2>&1 || exit 0

exec python3 -c '
import json, os, re, subprocess, sys

try:
    event = json.load(sys.stdin)
except ValueError:
    sys.exit(0)

session = re.sub(r"[^A-Za-z0-9_-]", "", str(event.get("session_id", "")))
path = (event.get("tool_input") or {}).get("file_path", "")
if not session or not path:
    sys.exit(0)

root = os.environ.get("CLAUDE_PROJECT_DIR") or event.get("cwd") or os.getcwd()
rel = os.path.relpath(os.path.abspath(path), root)
if rel.startswith(".."):
    sys.exit(0)  # outside the repo

try:
    git_dir = subprocess.run(
        ["git", "-C", root, "rev-parse", "--absolute-git-dir"],
        capture_output=True, text=True, timeout=10, check=True,
    ).stdout.strip()
except (OSError, subprocess.SubprocessError):
    sys.exit(0)

track_dir = os.path.join(git_dir, "claude-edits")
os.makedirs(track_dir, exist_ok=True)
with open(os.path.join(track_dir, session), "a") as f:
    f.write(rel + "\n")
'
