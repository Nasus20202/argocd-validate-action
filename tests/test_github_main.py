"""Tests for GitHub integration and main entry point."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from src.github import (
    _create_comment,
    _find_existing_comment,
    _update_comment,
    commit_state,
    post_pr_comment,
    write_step_summary,
)
from src.main import _is_truthy, parse_args


# ── GitHub integration tests ──


class TestPostPrComment:
    def test_no_token(self):
        assert post_pr_comment("body", "org/repo", "1", "") is False

    def test_no_pr_number(self):
        assert post_pr_comment("body", "org/repo", "", "token") is False

    @patch("src.github._find_existing_comment", return_value=None)
    @patch("src.github._create_comment", return_value=True)
    def test_create_new(self, mock_create, mock_find):
        result = post_pr_comment("body", "org/repo", "42", "token")
        assert result is True
        mock_create.assert_called_once()

    @patch("src.github._find_existing_comment", return_value="123")
    @patch("src.github._update_comment", return_value=True)
    def test_update_existing(self, mock_update, mock_find):
        result = post_pr_comment("body", "org/repo", "42", "token")
        assert result is True
        mock_update.assert_called_once()

    @patch("src.github._find_existing_comment", return_value=None)
    @patch("src.github._create_comment", return_value=False)
    def test_create_fails(self, mock_create, mock_find):
        result = post_pr_comment("body", "org/repo", "42", "token")
        assert result is False


class TestFindExistingComment:
    @patch("src.github.subprocess.run")
    def test_found(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                [
                    {"id": 111, "body": "unrelated"},
                    {"id": 222, "body": "## ArgoCD Manifest Changes\nstuff"},
                ]
            ),
        )
        result = _find_existing_comment(
            "https://api.github.com",
            "org/repo",
            "1",
            "token",
            "## ArgoCD Manifest Changes",
        )
        assert result == "222"

    @patch("src.github.subprocess.run")
    def test_not_found(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(
                [
                    {"id": 111, "body": "unrelated"},
                ]
            ),
        )
        result = _find_existing_comment(
            "https://api.github.com",
            "org/repo",
            "1",
            "token",
            "## ArgoCD Manifest Changes",
        )
        assert result is None

    @patch("src.github.subprocess.run")
    def test_api_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = _find_existing_comment(
            "https://api.github.com",
            "org/repo",
            "1",
            "token",
            "## ArgoCD Manifest Changes",
        )
        assert result is None


class TestUpdateComment:
    @patch("src.github.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert (
            _update_comment(
                "https://api.github.com", "org/repo", "123", "body", "token"
            )
            is True
        )

    @patch("src.github.subprocess.run")
    def test_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        assert (
            _update_comment(
                "https://api.github.com", "org/repo", "123", "body", "token"
            )
            is False
        )


class TestCreateComment:
    @patch("src.github.subprocess.run")
    def test_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        assert (
            _create_comment("https://api.github.com", "org/repo", "42", "body", "token")
            is True
        )

    @patch("src.github.subprocess.run")
    def test_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        assert (
            _create_comment("https://api.github.com", "org/repo", "42", "body", "token")
            is False
        )


class TestCommitState:
    def test_no_token(self, tmp_path):
        state = tmp_path / "state"
        state.mkdir()
        assert commit_state(state, "") is False

    def test_no_state_dir(self, tmp_path):
        assert commit_state(tmp_path / "nonexistent", "token") is False

    @patch("src.github._run_git")
    @patch("src.github.subprocess.run")
    def test_no_changes(self, mock_run, mock_git, tmp_path):
        state = tmp_path / "state"
        state.mkdir()
        # git diff --cached --quiet returns 0 = no changes
        mock_run.return_value = MagicMock(returncode=0)
        assert commit_state(state, "token") is True

    @patch("src.github._run_git")
    @patch("src.github.subprocess.run")
    def test_with_changes(self, mock_run, mock_git, tmp_path):
        state = tmp_path / "state"
        state.mkdir()
        # git diff --cached --quiet returns 1 = changes exist
        mock_run.return_value = MagicMock(returncode=1)
        assert commit_state(state, "token", "changed") is True
        # Should commit and push
        mock_git.assert_any_call("commit", "-m", "chore: update ArgoCD manifest state")
        mock_git.assert_any_call("push")

    @patch("src.github._run_git")
    @patch("src.github.subprocess.run")
    def test_initialize_message(self, mock_run, mock_git, tmp_path):
        state = tmp_path / "state"
        state.mkdir()
        mock_run.return_value = MagicMock(returncode=1)
        commit_state(state, "token", "initialized")
        mock_git.assert_any_call(
            "commit", "-m", "chore: initialize ArgoCD manifest state"
        )


class TestWriteStepSummary:
    def test_write_to_file(self, tmp_path):
        summary_file = tmp_path / "summary"
        summary_file.touch()
        write_step_summary("## Test", str(summary_file))
        assert "## Test" in summary_file.read_text()

    def test_append(self, tmp_path):
        summary_file = tmp_path / "summary"
        summary_file.write_text("existing\n")
        write_step_summary("new content", str(summary_file))
        content = summary_file.read_text()
        assert "existing" in content
        assert "new content" in content

    def test_no_summary_file(self, monkeypatch, capsys):
        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        write_step_summary("## Test")
        assert "## Test" in capsys.readouterr().out


# ── Main entry point tests ──


class TestIsTruthy:
    def test_true_values(self):
        assert _is_truthy("true")
        assert _is_truthy("True")
        assert _is_truthy("TRUE")
        assert _is_truthy("1")
        assert _is_truthy("yes")

    def test_false_values(self):
        assert not _is_truthy("false")
        assert not _is_truthy("False")
        assert not _is_truthy("0")
        assert not _is_truthy("no")
        assert not _is_truthy("")


class TestParseArgs:
    def test_defaults(self, monkeypatch):
        monkeypatch.delenv("INPUT_MANIFESTS_DIR", raising=False)
        monkeypatch.delenv("INPUT_MANIFESTS-DIR", raising=False)
        monkeypatch.delenv("INPUT_APPS_DIR", raising=False)
        monkeypatch.delenv("INPUT_APPS-DIR", raising=False)
        monkeypatch.delenv("INPUT_SKIP_FILES", raising=False)
        monkeypatch.delenv("INPUT_SKIP-FILES", raising=False)
        monkeypatch.delenv("INPUT_SKIP_RESOURCES", raising=False)
        monkeypatch.delenv("INPUT_SKIP-RESOURCES", raising=False)
        monkeypatch.delenv("INPUT_STATE_DIR", raising=False)
        monkeypatch.delenv("INPUT_STATE-DIR", raising=False)
        monkeypatch.delenv("INPUT_COMMENT_ON_PR", raising=False)
        monkeypatch.delenv("INPUT_COMMENT-ON-PR", raising=False)
        monkeypatch.delenv("INPUT_GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("INPUT_GITHUB-TOKEN", raising=False)
        monkeypatch.delenv("INPUT_COMMIT_STATE", raising=False)
        monkeypatch.delenv("INPUT_COMMIT-STATE", raising=False)
        monkeypatch.delenv("INPUT_VALIDATE_ARGO", raising=False)
        monkeypatch.delenv("INPUT_VALIDATE-ARGO", raising=False)
        monkeypatch.delenv("INPUT_KUBE_VERSION", raising=False)
        monkeypatch.delenv("INPUT_KUBE-VERSION", raising=False)
        monkeypatch.delenv("INPUT_SCHEMA_LOCATIONS", raising=False)
        monkeypatch.delenv("INPUT_SCHEMA-LOCATIONS", raising=False)
        monkeypatch.delenv("INPUT_VERBOSE", raising=False)

        args = parse_args([])
        assert args.manifests_dir == "manifests"
        assert args.apps_dir == ""
        assert args.skip_resources == "CustomResourceDefinition"
        assert args.validate_argo == "true"
        assert args.verbose is False

    def test_env_vars(self, monkeypatch):
        monkeypatch.setenv("INPUT_APPS_DIR", "/path/to/apps")
        monkeypatch.setenv("INPUT_VERBOSE", "true")
        args = parse_args([])
        assert args.apps_dir == "/path/to/apps"
        assert args.verbose is True

    def test_cli_overrides_env(self, monkeypatch):
        monkeypatch.setenv("INPUT_APPS_DIR", "/env/path")
        args = parse_args(["--apps-dir", "/cli/path"])
        assert args.apps_dir == "/cli/path"

    def test_dash_env_vars(self, monkeypatch):
        """GitHub Actions uses INPUT_FOO-BAR format."""
        monkeypatch.delenv("INPUT_APPS_DIR", raising=False)
        monkeypatch.setenv("INPUT_APPS-DIR", "/dash/path")
        args = parse_args([])
        assert args.apps_dir == "/dash/path"


class TestMain:
    @patch("src.main.discover_argo_manifests", return_value=([], []))
    @patch("src.main.validate_argo_manifests_dir")
    @patch("src.main.validate_k8s_manifests", return_value=(True, "OK"))
    @patch("src.main.write_step_summary")
    def test_no_apps_found(
        self, mock_summary, mock_k8s, mock_argo, mock_discover, tmp_path
    ):
        from src.main import main
        from src.validator import ValidationSummary

        mock_argo.return_value = ValidationSummary()

        apps_dir = tmp_path / "apps"
        apps_dir.mkdir()

        rc = main(
            [
                "--apps-dir",
                str(apps_dir),
                "--manifests-dir",
                str(tmp_path / "manifests"),
            ]
        )
        assert rc == 0

    def test_missing_apps_dir(self, monkeypatch):
        from src.main import main

        monkeypatch.delenv("INPUT_APPS_DIR", raising=False)
        monkeypatch.delenv("INPUT_APPS-DIR", raising=False)
        rc = main([])
        assert rc == 1
