#!/usr/bin/env bash
# Post or update a PR comment with ArgoCD manifest diff results.
# Used by the GitHub Action composite step.
#
# Required environment variables:
#   GH_TOKEN          - GitHub token for API access
#   PR_NUMBER         - Pull request number
#   GITHUB_REPOSITORY - Repository in "owner/repo" format
#   DIFF_STATUS       - Diff status ("changed", "unchanged", "initialized")
#
# Optional environment variables:
#   DIFF_COMMENT_FILE - Path to markdown file with comment body (when status=changed)

set -euo pipefail

COMMENT_MARKER="## ArgoCD Manifest Changes"

if [ -z "${GH_TOKEN:-}" ]; then
    echo "Warning: github-token is not set. Skipping PR comment."
    exit 0
fi

if [ -z "${PR_NUMBER:-}" ]; then
    echo "Not in a pull request context. Skipping PR comment."
    exit 0
fi

REPO="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
STATUS="${DIFF_STATUS:?DIFF_STATUS is required}"

# Build comment body
if [ "$STATUS" = "changed" ]; then
    COMMENT_FILE="${DIFF_COMMENT_FILE:-}"
    if [ -z "$COMMENT_FILE" ] || [ ! -f "$COMMENT_FILE" ]; then
        echo "No diff comment file found. Skipping PR comment."
        exit 0
    fi
    COMMENT_BODY=$(cat "$COMMENT_FILE")
else
    COMMENT_BODY="$COMMENT_MARKER

No changes detected in manifests."
fi

# Find existing comment
EXISTING_COMMENT_ID=$(curl -sf \
    -H "Authorization: token $GH_TOKEN" \
    "https://api.github.com/repos/$REPO/issues/$PR_NUMBER/comments?per_page=100" | \
    jq -r ".[] | select(.body | startswith(\"$COMMENT_MARKER\")) | .id" | head -1) || {
    echo "Warning: Failed to fetch existing PR comments."
    EXISTING_COMMENT_ID=""
}

PAYLOAD=$(jq -n --arg body "$COMMENT_BODY" '{body: $body}')

if [ -n "$EXISTING_COMMENT_ID" ] && [ "$EXISTING_COMMENT_ID" != "null" ]; then
    echo "Updating existing PR comment $EXISTING_COMMENT_ID..."
    curl -sf -X PATCH \
        -H "Authorization: token $GH_TOKEN" \
        -H "Content-Type: application/json" \
        "https://api.github.com/repos/$REPO/issues/comments/$EXISTING_COMMENT_ID" \
        -d "$PAYLOAD" > /dev/null || {
        echo "Warning: Failed to update PR comment."
        exit 0
    }
else
    echo "Creating new PR comment..."
    curl -sf -X POST \
        -H "Authorization: token $GH_TOKEN" \
        -H "Content-Type: application/json" \
        "https://api.github.com/repos/$REPO/issues/$PR_NUMBER/comments" \
        -d "$PAYLOAD" > /dev/null || {
        echo "Warning: Failed to create PR comment."
        exit 0
    }
fi

echo "PR comment posted successfully."
