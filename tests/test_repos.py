"""Tests for the repository resolver."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.repos import RepoResolver, _normalize_repo_url


class TestNormalizeRepoUrl:
    def test_https(self):
        assert (
            _normalize_repo_url("https://github.com/org/repo.git")
            == "github.com/org/repo"
        )

    def test_https_no_suffix(self):
        assert (
            _normalize_repo_url("https://github.com/org/repo") == "github.com/org/repo"
        )

    def test_ssh(self):
        assert (
            _normalize_repo_url("git@github.com:org/repo.git") == "github.com/org/repo"
        )

    def test_ssh_no_suffix(self):
        assert _normalize_repo_url("git@github.com:org/repo") == "github.com/org/repo"

    def test_git_protocol(self):
        assert (
            _normalize_repo_url("git://github.com/org/repo.git")
            == "github.com/org/repo"
        )

    def test_http(self):
        assert (
            _normalize_repo_url("http://github.com/org/repo.git")
            == "github.com/org/repo"
        )

    def test_trailing_slash(self):
        assert (
            _normalize_repo_url("https://github.com/org/repo/") == "github.com/org/repo"
        )

    def test_whitespace(self):
        assert (
            _normalize_repo_url("  https://github.com/org/repo  ")
            == "github.com/org/repo"
        )

    def test_different_hosts_same_path(self):
        assert (
            _normalize_repo_url("https://gitlab.com/org/repo") == "gitlab.com/org/repo"
        )


class TestRepoResolverInit:
    def test_explicit_local_repo_url(self, tmp_path):
        r = RepoResolver(
            workspace=tmp_path,
            local_repo_url="https://github.com/my-org/my-repo",
        )
        assert r._local_key == "github.com/my-org/my-repo"

    def test_detect_from_github_repository_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GITHUB_REPOSITORY", "my-org/my-repo")
        monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
        r = RepoResolver(workspace=tmp_path)
        assert r._local_key == "github.com/my-org/my-repo"

    def test_detect_from_github_repository_custom_server(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GITHUB_REPOSITORY", "my-org/my-repo")
        monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.example.com")
        r = RepoResolver(workspace=tmp_path)
        assert r._local_key == "github.example.com/my-org/my-repo"

    def test_no_env_no_git(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        r = RepoResolver(workspace=tmp_path)
        # No git remote in tmp_path, no env var → empty local key
        assert r._local_key == ""


class TestRepoResolverResolve:
    def test_empty_url_returns_workspace(self, tmp_path):
        r = RepoResolver(
            workspace=tmp_path, local_repo_url="https://github.com/org/repo"
        )
        assert r.resolve("", None) == tmp_path.resolve()

    def test_local_url_returns_workspace(self, tmp_path):
        r = RepoResolver(
            workspace=tmp_path, local_repo_url="https://github.com/org/repo"
        )
        assert r.resolve("https://github.com/org/repo", "main") == tmp_path.resolve()

    def test_local_url_ssh_format(self, tmp_path):
        r = RepoResolver(
            workspace=tmp_path, local_repo_url="https://github.com/org/repo"
        )
        # SSH URL for the same repo should match
        assert r.resolve("git@github.com:org/repo.git", "main") == tmp_path.resolve()

    def test_local_url_with_git_suffix(self, tmp_path):
        r = RepoResolver(
            workspace=tmp_path, local_repo_url="https://github.com/org/repo.git"
        )
        assert r.resolve("https://github.com/org/repo", None) == tmp_path.resolve()

    def test_external_url_triggers_clone(self, tmp_path):
        r = RepoResolver(
            workspace=tmp_path, local_repo_url="https://github.com/org/repo"
        )
        with patch.object(r, "_clone", return_value=tmp_path / "cloned") as mock_clone:
            result = r.resolve("https://github.com/other/repo", "v1.0")
            mock_clone.assert_called_once_with("https://github.com/other/repo", "v1.0")
            assert result == tmp_path / "cloned"

    def test_cache_prevents_re_clone(self, tmp_path):
        r = RepoResolver(
            workspace=tmp_path, local_repo_url="https://github.com/org/repo"
        )
        with patch.object(r, "_clone", return_value=tmp_path / "cloned") as mock_clone:
            r.resolve("https://github.com/other/repo", "v1.0")
            r.resolve("https://github.com/other/repo", "v1.0")
            # Should only clone once
            mock_clone.assert_called_once()

    def test_different_revisions_clone_separately(self, tmp_path):
        r = RepoResolver(
            workspace=tmp_path, local_repo_url="https://github.com/org/repo"
        )
        with patch.object(r, "_clone") as mock_clone:
            mock_clone.side_effect = [tmp_path / "v1", tmp_path / "v2"]
            r1 = r.resolve("https://github.com/other/repo", "v1.0")
            r2 = r.resolve("https://github.com/other/repo", "v2.0")
            assert r1 != r2
            assert mock_clone.call_count == 2


class TestRepoResolverCleanup:
    def test_cleanup_removes_temp_dir(self, tmp_path):
        r = RepoResolver(
            workspace=tmp_path, local_repo_url="https://github.com/org/repo"
        )
        # Force _tmp_root creation
        tmp_clones = tmp_path / "tmp_clones"
        r._tmp_root = tmp_clones
        r._tmp_root.mkdir()
        (r._tmp_root / "some_repo").mkdir()
        r.cleanup()
        assert not tmp_clones.exists()

    def test_context_manager(self, tmp_path):
        with RepoResolver(workspace=tmp_path, local_repo_url="x") as r:
            r._tmp_root = tmp_path / "tmp_clones"
            r._tmp_root.mkdir()
        assert not (tmp_path / "tmp_clones").exists()


class TestInjectToken:
    def test_https_url(self, tmp_path):
        r = RepoResolver(
            workspace=tmp_path,
            local_repo_url="x",
            github_token="ghp_abc123",
        )
        result = r._inject_token("https://github.com/org/repo")
        assert result == "https://x-access-token:ghp_abc123@github.com/org/repo"

    def test_no_token(self, tmp_path):
        r = RepoResolver(workspace=tmp_path, local_repo_url="x")
        result = r._inject_token("https://github.com/org/repo")
        assert result == "https://github.com/org/repo"

    def test_ssh_url_not_modified(self, tmp_path):
        r = RepoResolver(
            workspace=tmp_path,
            local_repo_url="x",
            github_token="ghp_abc123",
        )
        result = r._inject_token("git@github.com:org/repo.git")
        assert result == "git@github.com:org/repo.git"
