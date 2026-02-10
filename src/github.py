"""GitHub API integration for posting PR comments and committing state."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def post_pr_comment(
    comment_body: str,
    repo: str,
    pr_number: str | int,
    github_token: str,
    update_existing: bool = True,
    comment_marker: str = "## ArgoCD Manifest Changes",
) -> bool:
    """Post or update a PR comment on GitHub.

    Args:
        comment_body: The comment body (markdown)
        repo: Repository in "owner/repo" format
        pr_number: PR number
        github_token: GitHub token for authentication
        update_existing: Whether to update an existing comment
        comment_marker: String to identify existing comments to update

    Returns:
        True if successful
    """
    if not github_token:
        logger.warning("GitHub token not set, skipping PR comment")
        return False

    if not pr_number:
        logger.info("Not in a PR context, skipping PR comment")
        return False

    api_base = "https://api.github.com"
    headers = {
        "Authorization": f"token {github_token}",
        "Content-Type": "application/json",
        "Accept": "application/vnd.github.v3+json",
    }

    existing_comment_id = None
    if update_existing:
        existing_comment_id = _find_existing_comment(
            api_base, repo, pr_number, github_token, comment_marker
        )

    if existing_comment_id:
        logger.info("Updating existing PR comment %s...", existing_comment_id)
        success = _update_comment(
            api_base, repo, existing_comment_id, comment_body, github_token
        )
    else:
        logger.info("Creating new PR comment...")
        success = _create_comment(api_base, repo, pr_number, comment_body, github_token)

    if success:
        logger.info("PR comment posted successfully")
    else:
        logger.warning("Failed to post PR comment")

    return success


def _find_existing_comment(
    api_base: str,
    repo: str,
    pr_number: str | int,
    token: str,
    marker: str,
) -> str | None:
    """Find an existing comment with the given marker."""
    try:
        result = subprocess.run(
            [
                "curl",
                "-sf",
                "-H",
                f"Authorization: token {token}",
                "-H",
                "Accept: application/vnd.github.v3+json",
                f"{api_base}/repos/{repo}/issues/{pr_number}/comments?per_page=100",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None

        comments = json.loads(result.stdout)
        for comment in comments:
            if isinstance(comment, dict) and comment.get("body", "").startswith(marker):
                return str(comment["id"])
    except (json.JSONDecodeError, subprocess.TimeoutExpired, FileNotFoundError):
        logger.warning("Failed to fetch existing PR comments")

    return None


def _update_comment(
    api_base: str,
    repo: str,
    comment_id: str,
    body: str,
    token: str,
) -> bool:
    """Update an existing comment."""
    try:
        payload = json.dumps({"body": body})
        result = subprocess.run(
            [
                "curl",
                "-sf",
                "-X",
                "PATCH",
                "-H",
                f"Authorization: token {token}",
                "-H",
                "Content-Type: application/json",
                f"{api_base}/repos/{repo}/issues/comments/{comment_id}",
                "-d",
                payload,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _create_comment(
    api_base: str,
    repo: str,
    pr_number: str | int,
    body: str,
    token: str,
) -> bool:
    """Create a new comment."""
    try:
        payload = json.dumps({"body": body})
        result = subprocess.run(
            [
                "curl",
                "-sf",
                "-X",
                "POST",
                "-H",
                f"Authorization: token {token}",
                "-H",
                "Content-Type: application/json",
                f"{api_base}/repos/{repo}/issues/{pr_number}/comments",
                "-d",
                payload,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def commit_state(
    state_dir: Path,
    github_token: str,
    diff_status: str = "changed",
) -> bool:
    """Commit and push the state directory to the repository.

    Args:
        state_dir: Path to the state directory
        github_token: GitHub token for push
        diff_status: The diff status ("initialized" or "changed")

    Returns:
        True if successful
    """
    if not github_token:
        logger.warning("GitHub token not set, skipping state commit")
        return False

    if not state_dir.is_dir():
        logger.warning("State directory does not exist, skipping commit")
        return False

    try:
        # Configure git
        _run_git("config", "user.name", "github-actions[bot]")
        _run_git(
            "config",
            "user.email",
            "41898282+github-actions[bot]@users.noreply.github.com",
        )

        # Stage state directory
        _run_git("add", str(state_dir))

        # Check for changes
        result = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            capture_output=True,
            timeout=30,
        )
        if result.returncode == 0:
            logger.info("No changes to commit in state directory")
            return True

        # Commit
        message = (
            "chore: initialize ArgoCD manifest state"
            if diff_status == "initialized"
            else "chore: update ArgoCD manifest state"
        )
        _run_git("commit", "-m", message)

        # Push
        _run_git("push")
        logger.info("State committed and pushed to repository")
        return True

    except RuntimeError as e:
        logger.error(
            "Failed to commit state: %s. Ensure the github-token has "
            "write permissions and the workflow has 'contents: write' permission.",
            e,
        )
        return False


def _run_git(*args: str) -> str:
    """Run a git command and return stdout."""
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout


def write_step_summary(
    content: str,
    summary_file: str | None = None,
) -> None:
    """Write content to GitHub Actions step summary.

    Args:
        content: Markdown content to append
        summary_file: Path to GITHUB_STEP_SUMMARY (defaults to env var)
    """
    output_path = summary_file or os.environ.get("GITHUB_STEP_SUMMARY")
    if not output_path:
        logger.debug("GITHUB_STEP_SUMMARY not set, printing to stdout")
        print(content)
        return

    with open(output_path, "a") as f:
        f.write(content + "\n")
