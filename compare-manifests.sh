#!/bin/bash

set -e -u -o pipefail

STATE_DIR=${1:-"state"}
MANIFESTS_DIR=${2:-"manifests"}

echo "Comparing manifests..."
echo "State directory: $STATE_DIR"
echo "Manifests directory: $MANIFESTS_DIR"

if [ ! -d "$MANIFESTS_DIR" ]; then
    echo "Error: Manifests directory '$MANIFESTS_DIR' does not exist."
    exit 1
fi

# Validate state directory path to prevent accidental deletion of critical paths
if [ -n "$STATE_DIR" ]; then
    resolved=$(cd "$(dirname "$STATE_DIR")" 2>/dev/null && pwd)/$(basename "$STATE_DIR")
    if [ "$resolved" = "/" ] || [ "$resolved" = "/home" ] || [ "$resolved" = "/etc" ] || [ "$resolved" = "/usr" ]; then
        echo "Error: STATE_DIR resolves to a critical system path: $resolved"
        exit 1
    fi
fi

# If state directory does not exist or is empty, initialize it
if [ ! -d "$STATE_DIR" ] || [ -z "$(ls -A "$STATE_DIR" 2>/dev/null)" ]; then
    echo "State directory is empty or does not exist. Initializing with current manifests..."
    mkdir -p "$STATE_DIR"
    cp -r "$MANIFESTS_DIR"/* "$STATE_DIR"/ 2>/dev/null || true
    echo "State directory initialized."
    echo "DIFF_STATUS=initialized" >> "$GITHUB_OUTPUT"
    echo "DIFF_SUMMARY=State directory initialized with current manifests. No previous state to compare against." >> "$GITHUB_OUTPUT"
    exit 0
fi

# Generate diff between state and new manifests
DIFF_OUTPUT=$(diff -r -u "$STATE_DIR" "$MANIFESTS_DIR" 2>/dev/null || true)

if [ -z "$DIFF_OUTPUT" ]; then
    echo "No changes detected between state and new manifests."
    echo "DIFF_STATUS=unchanged" >> "$GITHUB_OUTPUT"
    echo "DIFF_SUMMARY=No changes detected in manifests." >> "$GITHUB_OUTPUT"
    exit 0
fi

echo "Changes detected between state and new manifests."
echo "DIFF_STATUS=changed" >> "$GITHUB_OUTPUT"

# Count additions, deletions, and modified files
# Use || true to handle grep returning non-zero when no matches found
ADDED_COUNT=$(diff -r -q "$STATE_DIR" "$MANIFESTS_DIR" 2>/dev/null | grep "^Only in $MANIFESTS_DIR" | wc -l || true)
REMOVED_COUNT=$(diff -r -q "$STATE_DIR" "$MANIFESTS_DIR" 2>/dev/null | grep "^Only in $STATE_DIR" | wc -l || true)
MODIFIED_COUNT=$(diff -r -q "$STATE_DIR" "$MANIFESTS_DIR" 2>/dev/null | grep "^Files .* differ$" | wc -l || true)

# Trim whitespace and ensure numeric values
ADDED_FILES=$(echo "$ADDED_COUNT" | tr -d ' \n')
REMOVED_FILES=$(echo "$REMOVED_COUNT" | tr -d ' \n')
MODIFIED_FILES=$(echo "$MODIFIED_COUNT" | tr -d ' \n')

# Default to 0 if empty
ADDED_FILES=${ADDED_FILES:-0}
REMOVED_FILES=${REMOVED_FILES:-0}
MODIFIED_FILES=${MODIFIED_FILES:-0}

# Build summary
SUMMARY="### Manifest Changes Summary\n"
SUMMARY="$SUMMARY\n| Type | Count |\n|------|-------|\n"
SUMMARY="$SUMMARY| Added | $ADDED_FILES |\n"
SUMMARY="$SUMMARY| Removed | $REMOVED_FILES |\n"
SUMMARY="$SUMMARY| Modified | $MODIFIED_FILES |\n"

# GitHub PR comment body limit is 65536 characters; use 60000 to leave room for markdown formatting
MAX_DIFF_LENGTH=60000
DIFF_LENGTH=${#DIFF_OUTPUT}

if [ "$DIFF_LENGTH" -gt "$MAX_DIFF_LENGTH" ]; then
    TRUNCATED_DIFF="${DIFF_OUTPUT:0:$MAX_DIFF_LENGTH}"
    DETAIL="\n<details>\n<summary>Detailed Diff (truncated - showing first ${MAX_DIFF_LENGTH} chars of ${DIFF_LENGTH})</summary>\n\n\`\`\`diff\n${TRUNCATED_DIFF}\n\`\`\`\n</details>"
else
    DETAIL="\n<details>\n<summary>Detailed Diff</summary>\n\n\`\`\`diff\n${DIFF_OUTPUT}\n\`\`\`\n</details>"
fi

FULL_COMMENT="## ArgoCD Manifest Changes\n${SUMMARY}${DETAIL}"

# Write the comment body to a file for the PR comment step
COMMENT_FILE=$(mktemp)
echo -e "$FULL_COMMENT" > "$COMMENT_FILE"
echo "DIFF_COMMENT_FILE=$COMMENT_FILE" >> "$GITHUB_OUTPUT"

# Also write a short summary
{
  echo "DIFF_SUMMARY<<EOF"
  echo "Added: $ADDED_FILES, Removed: $REMOVED_FILES, Modified: $MODIFIED_FILES"
  echo "EOF"
} >> "$GITHUB_OUTPUT"

# Update state directory with new manifests
rm -rf "${STATE_DIR:?}"/*
cp -r "$MANIFESTS_DIR"/* "$STATE_DIR"/ 2>/dev/null || true
echo "State directory updated with new manifests."
