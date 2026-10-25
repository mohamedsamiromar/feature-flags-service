#!/usr/bin/env bash
# PreToolUse on Bash for the read-only review subagents: allow `git diff` and
# `git log` only, with no shell operators, so a reviewer can read history but
# never change anything. Exit 2 blocks the call and tells the agent why.
# Fails closed: if the command cannot be parsed, it is blocked.

command -v python3 >/dev/null 2>&1 || { echo "readonly-git.sh: python3 not found; Bash blocked for this reviewer" >&2; exit 2; }

exec python3 -c '
import json, re, sys
try:
    command = (json.load(sys.stdin).get("tool_input") or {}).get("command", "")
except ValueError:
    command = ""
allowed = re.fullmatch(r"git (--no-pager )?(diff|log)( [^;&|<>`$\\\n]*)?", command.strip())
if not allowed or re.search(r"(^| )--(output|ext-diff)\b", command):
    print("Reviewers may only run `git diff ...` or `git log ...`, without pipes, "
          "redirects or chained commands. Use Read/Grep/Glob for everything else.", file=sys.stderr)
    sys.exit(2)
'
