#!/usr/bin/env bash
# Commit and push the manifest state directory to the repository.
#
# Required environment variables:
#   GH_TOKEN    - GitHub token with write permissions
#   STATE_DIR   - Path to the state directory
#   DIFF_STATUS - Diff status ("changed", "unchanged", "initialized")
set -euo pipefail

if [ -z "${GH_TOKEN:-}" ]; then
  echo "Warning: github-token is not set. Skipping state commit."
  exit 0
fi

STATE_DIR="${STATE_DIR:?STATE_DIR is required}"
STATUS="${DIFF_STATUS:?DIFF_STATUS is required}"

if [ ! -d "$STATE_DIR" ]; then
  echo "State directory does not exist. Skipping commit."
  exit 0
fi

git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add "$STATE_DIR"

if git diff --cached --quiet; then
  echo "No changes to commit in state directory."
  exit 0
fi

if [ "$STATUS" = "initialized" ]; then
  git commit -m "chore: initialize ArgoCD manifest state"
else
  git commit -m "chore: update ArgoCD manifest state"
fi

git push || {
  echo "Error: Failed to push state changes. Ensure the github-token has write permissions and the workflow has 'contents: write' permission."
  exit 1
}
echo "State committed and pushed to repository."
