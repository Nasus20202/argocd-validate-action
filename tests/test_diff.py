"""Tests for manifest diff/comparison logic."""

import os
from pathlib import Path

import pytest

from src.diff import (
    DiffSummary,
    compare_manifests,
    write_github_outputs,
    _compare_directories,
    _format_pr_comment,
    _validate_state_dir_path,
)


@pytest.fixture
def tmp_dir(tmp_path):
    return tmp_path


class TestDiffSummary:
    def test_default(self):
        s = DiffSummary()
        assert s.status == "unchanged"
        assert not s.has_changes
        assert s.added_count == 0

    def test_has_changes(self):
        s = DiffSummary(added_files=["new.yaml"])
        assert s.has_changes is True
        assert s.added_count == 1

    def test_format_summary_line(self):
        s = DiffSummary(
            added_files=["a.yaml"],
            removed_files=["b.yaml", "c.yaml"],
            modified_files=["d.yaml"],
        )
        line = s.format_summary_line()
        assert "Added: 1" in line
        assert "Removed: 2" in line
        assert "Modified: 1" in line


class TestCompareDirectories:
    def test_identical(self, tmp_dir):
        state = tmp_dir / "state"
        manifests = tmp_dir / "manifests"
        state.mkdir()
        manifests.mkdir()

        for d in [state, manifests]:
            (d / "app1").mkdir()
            (d / "app1" / "deploy.yaml").write_text("kind: Deployment")

        summary = _compare_directories(state, manifests)
        assert not summary.has_changes

    def test_added_files(self, tmp_dir):
        state = tmp_dir / "state"
        manifests = tmp_dir / "manifests"
        state.mkdir()
        manifests.mkdir()

        (state / "deploy.yaml").write_text("kind: Deployment")
        (manifests / "deploy.yaml").write_text("kind: Deployment")
        (manifests / "service.yaml").write_text("kind: Service")

        summary = _compare_directories(state, manifests)
        assert summary.has_changes
        assert summary.added_count == 1
        assert "service.yaml" in summary.added_files[0]

    def test_removed_files(self, tmp_dir):
        state = tmp_dir / "state"
        manifests = tmp_dir / "manifests"
        state.mkdir()
        manifests.mkdir()

        (state / "deploy.yaml").write_text("kind: Deployment")
        (state / "service.yaml").write_text("kind: Service")
        (manifests / "deploy.yaml").write_text("kind: Deployment")

        summary = _compare_directories(state, manifests)
        assert summary.has_changes
        assert summary.removed_count == 1

    def test_modified_files(self, tmp_dir):
        state = tmp_dir / "state"
        manifests = tmp_dir / "manifests"
        state.mkdir()
        manifests.mkdir()

        (state / "deploy.yaml").write_text("kind: Deployment")
        (manifests / "deploy.yaml").write_text("kind: StatefulSet")

        summary = _compare_directories(state, manifests)
        assert summary.has_changes
        assert summary.modified_count == 1

    def test_subdirectories(self, tmp_dir):
        state = tmp_dir / "state"
        manifests = tmp_dir / "manifests"

        (state / "app1").mkdir(parents=True)
        (manifests / "app1").mkdir(parents=True)
        (manifests / "app2").mkdir(parents=True)

        (state / "app1" / "deploy.yaml").write_text("kind: Deployment")
        (manifests / "app1" / "deploy.yaml").write_text("kind: Deployment")
        (manifests / "app2" / "service.yaml").write_text("kind: Service")

        summary = _compare_directories(state, manifests)
        assert summary.added_count == 1


class TestCompareManifests:
    def test_initialize_empty_state(self, tmp_dir):
        state = tmp_dir / "state"
        manifests = tmp_dir / "manifests"
        manifests.mkdir()
        (manifests / "app1").mkdir()
        (manifests / "app1" / "deploy.yaml").write_text("kind: Deployment")

        summary = compare_manifests(state, manifests, update_state=True)
        assert summary.status == "initialized"
        assert (state / "app1" / "deploy.yaml").exists()

    def test_initialize_empty_dir(self, tmp_dir):
        state = tmp_dir / "state"
        state.mkdir()
        manifests = tmp_dir / "manifests"
        manifests.mkdir()
        (manifests / "deploy.yaml").write_text("kind: Deployment")

        summary = compare_manifests(state, manifests)
        assert summary.status == "initialized"

    def test_no_changes(self, tmp_dir):
        state = tmp_dir / "state"
        manifests = tmp_dir / "manifests"
        state.mkdir()
        manifests.mkdir()
        (state / "deploy.yaml").write_text("kind: Deployment")
        (manifests / "deploy.yaml").write_text("kind: Deployment")

        summary = compare_manifests(state, manifests, update_state=False)
        assert summary.status == "unchanged"

    def test_changes_detected(self, tmp_dir):
        state = tmp_dir / "state"
        manifests = tmp_dir / "manifests"
        state.mkdir()
        manifests.mkdir()
        (state / "deploy.yaml").write_text("kind: Deployment")
        (manifests / "deploy.yaml").write_text("kind: StatefulSet")

        summary = compare_manifests(state, manifests, update_state=False)
        assert summary.status == "changed"
        assert summary.modified_count == 1
        assert summary.comment_body  # Non-empty
        assert "ArgoCD Manifest Changes" in summary.comment_body

    def test_manifests_dir_missing(self, tmp_dir):
        state = tmp_dir / "state"
        with pytest.raises(FileNotFoundError):
            compare_manifests(state, tmp_dir / "nonexistent")

    def test_state_updated_after_comparison(self, tmp_dir):
        state = tmp_dir / "state"
        manifests = tmp_dir / "manifests"
        state.mkdir()
        manifests.mkdir()
        (state / "deploy.yaml").write_text("kind: Deployment")
        (manifests / "deploy.yaml").write_text("kind: StatefulSet")

        compare_manifests(state, manifests, update_state=True)
        # State should now match manifests
        assert (state / "deploy.yaml").read_text() == "kind: StatefulSet"

    def test_added_and_removed(self, tmp_dir):
        state = tmp_dir / "state"
        manifests = tmp_dir / "manifests"
        state.mkdir()
        manifests.mkdir()

        (state / "old.yaml").write_text("old")
        (manifests / "new.yaml").write_text("new")

        summary = compare_manifests(state, manifests, update_state=False)
        assert summary.added_count == 1
        assert summary.removed_count == 1


class TestValidateStateDirPath:
    def test_safe_path(self, tmp_dir):
        _validate_state_dir_path(tmp_dir / "state")  # Should not raise

    def test_critical_root(self):
        with pytest.raises(ValueError):
            _validate_state_dir_path(Path("/"))

    def test_critical_home(self):
        with pytest.raises(ValueError):
            _validate_state_dir_path(Path("/home"))

    def test_critical_etc(self):
        with pytest.raises(ValueError):
            _validate_state_dir_path(Path("/etc"))


class TestFormatPrComment:
    def test_basic_format(self):
        summary = DiffSummary(
            added_files=["new.yaml"],
            removed_files=["old.yaml"],
            modified_files=["changed.yaml"],
            diff_text="--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new",
        )
        comment = _format_pr_comment(summary)
        assert "ArgoCD Manifest Changes" in comment
        assert "Added" in comment
        assert "Removed" in comment
        assert "Modified" in comment
        assert "new.yaml" in comment
        assert "old.yaml" in comment
        assert "Detailed Diff" in comment

    def test_truncation(self):
        summary = DiffSummary(
            modified_files=["big.yaml"],
            diff_text="x" * 100000,
        )
        comment = _format_pr_comment(summary)
        assert "truncated" in comment


class TestWriteGithubOutputs:
    def test_write_initialized(self, tmp_dir):
        output_file = tmp_dir / "github_output"
        output_file.touch()

        summary = DiffSummary(status="initialized")
        write_github_outputs(summary, str(output_file))

        content = output_file.read_text()
        assert "diff-status=initialized" in content

    def test_write_unchanged(self, tmp_dir):
        output_file = tmp_dir / "github_output"
        output_file.touch()

        summary = DiffSummary(status="unchanged")
        write_github_outputs(summary, str(output_file))

        content = output_file.read_text()
        assert "diff-status=unchanged" in content
        assert "No changes detected" in content

    def test_write_changed(self, tmp_dir):
        output_file = tmp_dir / "github_output"
        output_file.touch()

        summary = DiffSummary(
            status="changed",
            added_files=["a.yaml"],
            modified_files=["b.yaml"],
            comment_body="## ArgoCD Manifest Changes\ntest",
        )
        write_github_outputs(summary, str(output_file))

        content = output_file.read_text()
        assert "diff-status=changed" in content
        assert "diff-comment-file=" in content
        assert "Added: 1" in content

    def test_no_output_file(self, tmp_dir):
        # Should not raise even without output file
        summary = DiffSummary(status="unchanged")
        write_github_outputs(summary, None)
