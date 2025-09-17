#!/usr/bin/env bash
set -euo pipefail

# Simple autocommit script: stages changes, runs formatting, commits and pushes.
ROOT_DIR=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT_DIR"

if ! git rev-parse --is-inside-work-tree > /dev/null 2>&1; then
  echo "Not a git repository"
  exit 1
fi

# Run formatters if available
if command -v .venv/bin/black > /dev/null 2>&1; then
  .venv/bin/black . || true
fi
if command -v .venv/bin/isort > /dev/null 2>&1; then
  .venv/bin/isort . || true
fi

git add -A
if git diff --cached --quiet; then
  echo "No changes to commit"
  exit 0
fi

MSG="chore: auto-commit changes ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
git commit -m "$MSG"
git push origin HEAD

echo "Committed and pushed: $MSG"
