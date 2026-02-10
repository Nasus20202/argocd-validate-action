#!/usr/bin/env bash
# Append ArgoCD manifest diff information to the GitHub Actions job summary.
#
# Required environment variables:
#   GITHUB_STEP_SUMMARY - Path to the step summary file (set by GitHub Actions)
#   DIFF_STATUS         - Diff status ("changed", "unchanged", "initialized")
#
# Optional environment variables:
#   DIFF_COMMENT_FILE   - Path to markdown file with comment body (when status=changed)
#   DIFF_SUMMARY        - Short summary text (for initialized/unchanged)
set -euo pipefail

STATUS="${DIFF_STATUS:?DIFF_STATUS is required}"
SUMMARY="${DIFF_SUMMARY:-}"
COMMENT_FILE="${DIFF_COMMENT_FILE:-}"

echo "" >> "$GITHUB_STEP_SUMMARY"

if [ "$STATUS" = "changed" ]; then
  if [ -n "$COMMENT_FILE" ] && [ -f "$COMMENT_FILE" ]; then
    cat "$COMMENT_FILE" >> "$GITHUB_STEP_SUMMARY"
  fi
elif [ "$STATUS" = "initialized" ]; then
  echo "## ArgoCD Manifest State" >> "$GITHUB_STEP_SUMMARY"
  echo "$SUMMARY" >> "$GITHUB_STEP_SUMMARY"
else
  echo "## ArgoCD Manifest Changes" >> "$GITHUB_STEP_SUMMARY"
  echo "$SUMMARY" >> "$GITHUB_STEP_SUMMARY"
fi
