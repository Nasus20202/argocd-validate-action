"""Compare manifest directories and generate diff reports."""

from __future__ import annotations

import filecmp
import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Maximum diff length for PR comments (GitHub limit is 65536)
MAX_DIFF_LENGTH = 60000


@dataclass
class DiffSummary:
    """Summary of differences between state and new manifests."""

    status: str = "unchanged"  # "initialized", "unchanged", "changed"
    added_files: list[str] = field(default_factory=list)
    removed_files: list[str] = field(default_factory=list)
    modified_files: list[str] = field(default_factory=list)
    diff_text: str = ""
    comment_body: str = ""

    @property
    def added_count(self) -> int:
        return len(self.added_files)

    @property
    def removed_count(self) -> int:
        return len(self.removed_files)

    @property
    def modified_count(self) -> int:
        return len(self.modified_files)

    @property
    def has_changes(self) -> bool:
        return bool(self.added_files or self.removed_files or self.modified_files)

    def format_summary_line(self) -> str:
        """One-line summary for GITHUB_OUTPUT."""
        return (
            f"Added: {self.added_count}, "
            f"Removed: {self.removed_count}, "
            f"Modified: {self.modified_count}"
        )


def compare_manifests(
    state_dir: Path,
    manifests_dir: Path,
    update_state: bool = True,
) -> DiffSummary:
    """Compare state directory with newly generated manifests.

    Args:
        state_dir: Directory containing previous manifest state
        manifests_dir: Directory containing newly generated manifests
        update_state: Whether to update state_dir with new manifests after comparison

    Returns:
        DiffSummary with change details
    """
    if not manifests_dir.is_dir():
        raise FileNotFoundError(
            f"Manifests directory '{manifests_dir}' does not exist."
        )

    # Validate state directory path
    _validate_state_dir_path(state_dir)

    # If state directory doesn't exist or is empty, initialize it
    if not state_dir.is_dir() or not any(state_dir.iterdir()):
        return _initialize_state(state_dir, manifests_dir)

    # Compare directories
    summary = _compare_directories(state_dir, manifests_dir)

    if not summary.has_changes:
        summary.status = "unchanged"
        logger.info("No changes detected between state and new manifests.")
    else:
        summary.status = "changed"
        summary.diff_text = _generate_diff_text(state_dir, manifests_dir)
        summary.comment_body = _format_pr_comment(summary)
        logger.info(
            "Changes detected: %d added, %d removed, %d modified",
            summary.added_count,
            summary.removed_count,
            summary.modified_count,
        )

    # Update state directory
    if update_state:
        _update_state_dir(state_dir, manifests_dir)

    return summary


def _validate_state_dir_path(state_dir: Path) -> None:
    """Validate that state directory path is safe."""
    resolved = state_dir.resolve()
    critical_paths = {
        Path("/"),
        Path("/home"),
        Path("/etc"),
        Path("/usr"),
        Path("/var"),
        Path("/tmp"),
    }
    if resolved in critical_paths:
        raise ValueError(f"STATE_DIR resolves to a critical system path: {resolved}")


def _initialize_state(state_dir: Path, manifests_dir: Path) -> DiffSummary:
    """Initialize state directory with current manifests."""
    logger.info("State directory is empty or does not exist. Initializing...")
    state_dir.mkdir(parents=True, exist_ok=True)

    # Copy manifests to state
    for item in manifests_dir.iterdir():
        dest = state_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dest)

    summary = DiffSummary(
        status="initialized",
        comment_body=(
            "## ArgoCD Manifest Changes\n\n"
            "State directory initialized with current manifests. "
            "No previous state to compare against."
        ),
    )
    logger.info("State directory initialized.")
    return summary


def _compare_directories(state_dir: Path, manifests_dir: Path) -> DiffSummary:
    """Recursively compare two directories."""
    summary = DiffSummary()

    state_files = _collect_files(state_dir)
    manifest_files = _collect_files(manifests_dir)

    state_rel = {f.relative_to(state_dir) for f in state_files}
    manifest_rel = {f.relative_to(manifests_dir) for f in manifest_files}

    # Added files (in manifests but not in state)
    for rel in sorted(manifest_rel - state_rel):
        summary.added_files.append(str(rel))

    # Removed files (in state but not in manifests)
    for rel in sorted(state_rel - manifest_rel):
        summary.removed_files.append(str(rel))

    # Modified files (in both but different content)
    for rel in sorted(state_rel & manifest_rel):
        state_file = state_dir / rel
        manifest_file = manifests_dir / rel
        if not filecmp.cmp(state_file, manifest_file, shallow=False):
            summary.modified_files.append(str(rel))

    return summary


def _collect_files(directory: Path) -> list[Path]:
    """Recursively collect all files in a directory."""
    files = []
    if directory.is_dir():
        for item in directory.rglob("*"):
            if item.is_file():
                files.append(item)
    return files


def _generate_diff_text(state_dir: Path, manifests_dir: Path) -> str:
    """Generate unified diff text between two directories."""
    import subprocess

    try:
        result = subprocess.run(
            ["diff", "-r", "-u", str(state_dir), str(manifests_dir)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result.stdout or ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return "(diff command not available)"


def _format_pr_comment(summary: DiffSummary) -> str:
    """Format the diff summary as a GitHub PR comment."""
    lines = ["## ArgoCD Manifest Changes\n"]
    lines.append("### Manifest Changes Summary\n")
    lines.append("| Type | Count |")
    lines.append("|------|-------|")
    lines.append(f"| Added | {summary.added_count} |")
    lines.append(f"| Removed | {summary.removed_count} |")
    lines.append(f"| Modified | {summary.modified_count} |")
    lines.append("")

    # Add details for each category
    if summary.added_files:
        lines.append("**Added files:**")
        for f in summary.added_files:
            lines.append(f"- `{f}`")
        lines.append("")

    if summary.removed_files:
        lines.append("**Removed files:**")
        for f in summary.removed_files:
            lines.append(f"- `{f}`")
        lines.append("")

    if summary.modified_files:
        lines.append("**Modified files:**")
        for f in summary.modified_files:
            lines.append(f"- `{f}`")
        lines.append("")

    # Add detailed diff
    diff_text = summary.diff_text
    if diff_text:
        diff_length = len(diff_text)
        if diff_length > MAX_DIFF_LENGTH:
            truncated = diff_text[:MAX_DIFF_LENGTH]
            lines.append(
                f"\n<details>\n<summary>Detailed Diff "
                f"(truncated - showing first {MAX_DIFF_LENGTH} chars of "
                f"{diff_length})</summary>\n\n```diff\n{truncated}\n```\n</details>"
            )
        else:
            lines.append(
                f"\n<details>\n<summary>Detailed Diff</summary>"
                f"\n\n```diff\n{diff_text}\n```\n</details>"
            )

    return "\n".join(lines)


def _update_state_dir(state_dir: Path, manifests_dir: Path) -> None:
    """Update state directory with new manifests."""
    # Clear old state
    if state_dir.is_dir():
        for item in state_dir.iterdir():
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()

    # Copy new manifests
    for item in manifests_dir.iterdir():
        dest = state_dir / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dest)

    logger.info("State directory updated with new manifests.")


def write_github_outputs(summary: DiffSummary, output_file: str | None = None) -> None:
    """Write diff results to GITHUB_OUTPUT file.

    Args:
        summary: The diff summary to write
        output_file: Path to GITHUB_OUTPUT file (defaults to env var)
    """
    output_path = output_file or os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        logger.warning("GITHUB_OUTPUT not set, skipping output writing")
        return

    with open(output_path, "a") as f:
        f.write(f"diff-status={summary.status}\n")

        if summary.status == "initialized":
            f.write(
                "diff-summary=State directory initialized with current "
                "manifests. No previous state to compare against.\n"
            )
        elif summary.status == "changed":
            # Write comment file
            import tempfile

            comment_file = tempfile.NamedTemporaryFile(
                mode="w", suffix=".md", delete=False, prefix="argocd-diff-"
            )
            comment_file.write(summary.comment_body)
            comment_file.close()
            f.write(f"diff-comment-file={comment_file.name}\n")

            # Write summary using heredoc
            f.write("diff-summary<<EOF\n")
            f.write(summary.format_summary_line() + "\n")
            f.write("EOF\n")
        else:
            f.write("diff-summary=No changes detected in manifests.\n")
